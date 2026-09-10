    # ------------------------------------------- V8 spatial partitioning

    def _execute_durable_partitioned(
        self,
        run: ExecutionRun,
        node: ExecutionNode,
        outputs: dict[str, dict[str, Any]],
        outputs_fp: dict[str, str],
        ev: NodeEvidence,
        *,
        session_id: Optional[str],
        owner_scope: str,
        cancel_token: Optional[CancellationToken],
        node_deadline: float,
        governor: Optional[Any],
        gov_path: Optional[str],
        charge_ledger: Optional[Any] = None,
        budget: Any = None,
        resource_envelope: Optional[dict[str, Any]] = None,
        emit_events: bool = False,
    ) -> None:
        """分区 fan-out 执行（V8 Phase D，ADR-0130 §4）。

        输入形状（内存中的上游输出）→ 空间分区计划 → N 个 tile job
        （每个是普通 durable job：独立幂等键/独立重试/独立放置）→
        逐 tile 收敛（stale 可重派；failed 为该 tile 终局）→ seam 合并
        （vector：内容去重；raster：core 窗口写回，halo 裁除）。

        复用与普通 durable 节点同层（合并结果按节点语义指纹记录 ——
        分区方案进指纹，tile 变化自然失效）。取消/deadline 级联到全部
        在飞 tile job（await_node_jobs 持久取消）。
        """
        started_pt = time.monotonic()
        from app.services.geocompute import partitioning as P

        def _fail(code: str, message: str) -> None:
            ev.status = "failed"
            ev.error_code = code
            ev.error_message = _scrub_error_message(message)
            ev.retry_safe = False
            ev.duration_s = round(time.monotonic() - started_pt, 6)
            _emit_run_event(run.run_id, "node_failed", enabled=emit_events,
                            node_id=node.node_id, error_code=code)

        try:
            P.ensure_partition_supported(node)
        except GeoComputeError as exc:
            _fail(exc.code, str(exc))
            return
        if node.reuse == NodeReusePolicy.ALLOW and self._durable_reuse_hit(
            run, node, outputs, outputs_fp, ev, owner_scope,
            governor=governor, gov_path=gov_path, charge_ledger=charge_ledger,
            emit_events=emit_events,
        ):
            ev.duration_s = round(time.monotonic() - started_pt, 6)
            return
        try:
            plan = self._build_partition_plan(node, outputs)
        except RasterioUnavailableError as exc:
            _fail("RASTERIO_UNAVAILABLE", str(exc))
            return
        except GeoComputeError as exc:
            _fail(exc.code, str(exc))
            return
        _emit_run_event(run.run_id, "partition_planned", enabled=emit_events,
                        node_id=node.node_id,
                        status=f"tiles={plan.tiles}", rows=plan.tiles)
        tracing.emit("partition_planned", run_id=run.run_id,
                     node_id=node.node_id, tiles=plan.tiles,
                     scheme=plan.scheme)
        _emit_run_event(run.run_id, "node_dispatched", enabled=emit_events,
                        node_id=node.node_id, attempt=1, status="running")

        input_refs = {
            src: str(outputs[src]["ref_id"])
            for src in node.inputs
            if src in outputs and outputs[src].get("ref_id")
        }
        input_keys = {
            src: outputs_fp[src] for src in node.inputs if src in outputs_fp
        }
        max_tile_attempts = max(1, int(node.retry.max_attempts))
        results: dict[int, dict[str, Any]] = {}
        outstanding: dict[int, dict[str, Any]] = {}
        for part in plan.parts:
            entry: dict[str, Any] = {
                "attempts": 1,
                "node": self._tile_node(node, part, plan.scheme, plan.tiles),
                "job_id": None,
                "dispatch_failed": False,
            }
            job_id = self._dispatch_tile(
                entry["node"], run, session_id, node_deadline, budget,
                input_refs, input_keys, resource_envelope, owner_scope,
            )
            if job_id is None:
                entry["attempts"] += 1
                entry["dispatch_failed"] = True
            else:
                entry["job_id"] = job_id
            outstanding[part.index] = entry

        from app.services.geocompute import durable as _durable

        while outstanding:
            live_ids = [e["job_id"] for e in outstanding.values()
                        if e.get("job_id") is not None]
            try:
                states = _durable.await_node_jobs(
                    live_ids,
                    session_id=session_id or "",
                    deadline_ts=node_deadline,
                    cancel_token=cancel_token if node.cancellable else None,
                )
            except OperationCancelled:
                ev.status = "cancelled"
                ev.error_code = "CANCELLED"
                ev.duration_s = round(time.monotonic() - started_pt, 6)
                tracing.emit("node_cancelled", run_id=run.run_id,
                             node_id=node.node_id, status="cancelled",
                             policy="durable_job")
                _emit_run_event(run.run_id, "node_cancelled",
                                enabled=emit_events, node_id=node.node_id,
                                error_code="CANCELLED")
                return
            except GeoComputeError as exc:
                # deadline：await 已对在飞 tile 请求持久取消
                _fail(exc.code, str(exc))
                return
            still: dict[int, dict[str, Any]] = {}
            for idx, entry in list(outstanding.items()):
                st = states.get(entry["job_id"]) \
                    if entry["job_id"] is not None else None
                if entry["dispatch_failed"]:
                    st = {"status": "failed", "payload": {},
                          "error": "dispatch failed"}
                if st is None:
                    still[idx] = entry
                    continue
                if st["status"] == "completed":
                    results[idx] = st["payload"]
                    continue
                # stale/cancelled → 可重派（worker 丢失语义）；failed = 终局
                retryable = st["status"] in {"stale", "cancelled"} \
                    and entry["attempts"] < max_tile_attempts
                if retryable:
                    entry["attempts"] += 1
                    entry["dispatch_failed"] = False
                    tile_node = self._tile_node(
                        node, plan.parts_by_index[idx],
                        plan.scheme, plan.tiles,
                    )
                    entry["node"] = tile_node
                    new_job = self._dispatch_tile(
                        tile_node, run, session_id, node_deadline, budget,
                        input_refs, input_keys, resource_envelope, owner_scope,
                    )
                    if new_job is not None:
                        entry["job_id"] = new_job
                    else:
                        entry["dispatch_failed"] = True
                    still[idx] = entry
                    continue
                _fail(
                    "PARTITION_TILE_LOST"
                    if st["status"] in {"stale", "cancelled"} else "TILE_FAILED",
                    f"tile {idx} ({st['status']}): "
                    f"{st.get('error') or 'unknown'}",
                )
                return
            outstanding = still

        # ── seam 合并 ──
        try:
            payload = self._merge_partition_results(
                node, plan, results, session_id=session_id,
                cancel_token=cancel_token,
            )
        except RasterioUnavailableError as exc:
            _fail("RASTERIO_UNAVAILABLE", str(exc))
            return
        except GeoComputeError as exc:
            _fail(exc.code, str(exc))
            return
        except OperationCancelled:
            ev.status = "cancelled"
            ev.error_code = "CANCELLED"
            ev.duration_s = round(time.monotonic() - started_pt, 6)
            return
        outputs[node.node_id] = payload
        ev.status = "completed"
        ev.rows_emitted = self._count_rows(payload)
        ev.duration_s = round(time.monotonic() - started_pt, 6)
        ev.output_ref = payload.get("ref_id")
        meta = payload.get("metadata") or {}
        ev.output_summary = {
            k: v for k, v in meta.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        }
        tracing.emit("node_completed", run_id=run.run_id,
                     node_id=node.node_id, status="completed",
                     rows=ev.rows_emitted, duration_s=ev.duration_s,
                     policy="durable_job", tiles=plan.tiles)
        _emit_run_event(run.run_id, "node_completed", enabled=emit_events,
                        node_id=node.node_id, rows=ev.rows_emitted)
        self._governor_charge(governor, gov_path, node, payload, charge_ledger)
        self._record_durable_result(
            run, node, outputs, outputs_fp, owner_scope,
            session_id=session_id, payload=payload,
        )

    def _build_partition_plan(
        self, node: ExecutionNode, outputs: dict[str, dict[str, Any]]
    ) -> Any:
        """输入形状 → 分区计划（纯函数；形状不可得 = 类型化失败）。"""
        from app.services.geocompute import partitioning as P

        spec = node.partition
        est = node.estimate
        est_mem = float(getattr(est, "memory_mb", None) or 0) if est else 0.0
        if spec.scheme == "raster_grid":
            raster_path = None
            for src in node.inputs:
                rp = outputs.get(src, {}).get("raster_path")
                if rp:
                    raster_path = str(rp)
                    break
            if not raster_path:
                raise NodeExecutionError(
                    "raster partition requires an upstream raster_path input",
                    retry_safe=False, node_id=node.node_id,
                    details={"reason": "PARTITION_INPUT_UNRESOLVABLE"},
                )
            from app.lib.geo_analysis.raster_mosaic import raster_header

            head = raster_header(raster_path)
            plan = P.plan_raster(
                spec, width=int(head["width"]), height=int(head["height"]),
                crs=head.get("crs"),
            )
            plan.meta["header"] = {
                "width": int(head["width"]), "height": int(head["height"]),
                "crs": head.get("crs"), "transform": head.get("transform"),
            }
            return plan
        # vector_grid
        features = None
        for src in node.inputs:
            feats = outputs.get(src, {}).get("features")
            if isinstance(feats, list):
                features = feats
                break
        if features is None:
            raise NodeExecutionError(
                "vector partition requires an upstream inline features input",
                retry_safe=False, node_id=node.node_id,
                details={"reason": "PARTITION_INPUT_UNRESOLVABLE"},
            )
        if not features:
            raise NodeExecutionError(
                "vector partition input is empty",
                retry_safe=False, node_id=node.node_id,
                details={"reason": "PARTITION_INPUT_EMPTY"},
            )
        xs: list[float] = []
        ys: list[float] = []
        for f in features:
            pt = P.representative_point(f)
            if pt is not None:
                xs.append(pt[0])
                ys.append(pt[1])
        if not xs:
            raise NodeExecutionError(
                "vector partition input has no resolvable geometries",
                retry_safe=False, node_id=node.node_id,
                details={"reason": "PARTITION_INPUT_EMPTY"},
            )
        bbox = (min(xs), min(ys), max(xs), max(ys))
        tiles = P.adaptive_tile_count(
            spec, est_total_mem_mb=est_mem or None, input_rows=len(features))
        spec_eff = spec.model_copy(update={"target_tiles": tiles})
        return P.plan_vector(
            spec_eff, bbox=bbox, crs=spec.crs, input_rows=len(features),
        )

    @staticmethod
    def _tile_node(
        node: ExecutionNode, part: Any, scheme: str, count: int
    ) -> ExecutionNode:
        """分区 job 的节点副本（``_partition`` 进参数 → 独立幂等键）。"""
        if scheme == "raster_grid":
            part_meta: dict[str, Any] = {
                "index": part.index, "count": count, "scheme": scheme,
                "window": part.window(),
            }
        else:
            part_meta = {
                "index": part.index, "count": count, "scheme": scheme,
                "halo_bbox": list(part.halo_bbox),
            }
        return node.model_copy(update={
            "parameters": {**node.parameters, "_partition": part_meta},
        })

    def _dispatch_tile(
        self,
        tile_node: ExecutionNode,
        run: ExecutionRun,
        session_id: Optional[str],
        node_deadline: float,
        budget: Any,
        input_refs: dict[str, str],
        input_keys: dict[str, str],
        resource_envelope: Optional[dict[str, Any]],
        owner_scope: str,
    ) -> Optional[int]:
        """派发单个 tile job；返回 job_id（派发失败 None → tile 重试）。"""
        from app.services.geocompute import durable

        ret = durable.dispatch_node(
            tile_node,
            session_id=session_id or "",
            plan_fingerprint=run.plan_fingerprint,
            deadline_s=(node_deadline - time.monotonic())
            if node.deadline_s is not None else None,
            budget=budget,
            run_id=run.run_id,
            node_attempt=1,
            input_refs=input_refs,
            input_keys=input_keys,
            resource_envelope=resource_envelope,
            owner_scope=owner_scope,
        )
        try:
            return int(ret.get("job_id"))
        except (TypeError, ValueError):
            return None

    def _merge_partition_results(
        self,
        node: ExecutionNode,
        plan: Any,
        results: dict[int, dict[str, Any]],
        *,
        session_id: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> dict[str, Any]:
        """tile 结果 → 单一节点输出（seam 语义的唯一执行点）。"""
        from app.services.geocompute import partitioning as P

        if cancel_token is not None and cancel_token.cancelled:
            raise OperationCancelled("partition merge cancelled")
        meta: dict[str, Any] = {
            "partition_tiles": plan.tiles, "partition_scheme": plan.scheme,
        }
        if plan.crs:
            meta["partition_crs"] = str(plan.crs)
        if plan.scheme == "vector_grid":
            missing = [p.index for p in plan.parts if p.index not in results]
            if missing:
                raise NodeExecutionError(
                    f"tiles produced no result: {missing[:8]}",
                    retry_safe=False, node_id=node.node_id,
                )
            ordered = [results[i] for i in sorted(results)]
            payload = P.merge_vector_payloads(ordered, node_id=node.node_id)
            merge_meta = payload.get("metadata", {}).get("partition_merge", {})
            meta["partition_dedup_removed"] = int(
                merge_meta.get("dedup_removed", 0))
            skew = P.detect_skew([
                len(p.get("features") or []) for p in ordered
            ])
            if skew:
                meta["partition_skew_ratio"] = skew["ratio"]
            ref_id = self._store_merged_payload(node, payload, session_id)
            if ref_id:
                payload["ref_id"] = ref_id
            payload["metadata"] = meta
            return payload
        # raster_grid：core 窗口写回（halo 裁除）
        head = plan.meta.get("header") or {}
        tiles: list[dict[str, Any]] = []
        for part in plan.parts:
            payload = results.get(part.index)
            if not payload or not payload.get("raster_path"):
                raise NodeExecutionError(
                    f"tile {part.index} produced no raster output",
                    retry_safe=False, node_id=node.node_id,
                )
            tiles.append({
                "path": str(payload["raster_path"]),
                "core_window": part.core_window(),
            })
        out_dir = os.path.dirname(str(tiles[0]["path"])) or "."
        out_path = os.path.join(
            out_dir, f"merged-{node.semantic_fingerprint()}.tif")
        merged = P.merge_raster_tiles(
            tiles, out_path=out_path,
            width=int(head.get("width", 0)), height=int(head.get("height", 0)),
            crs=head.get("crs"), transform=head.get("transform"),
        )
        meta["raster_op"] = f"partitioned:{node.operation or 'raster'}"
        return {"raster_path": merged["output_path"], "metadata": meta}

    def _store_merged_payload(
        self, node: ExecutionNode, payload: dict[str, Any],
        session_id: Optional[str],
    ) -> Optional[str]:
        """合并后的 vector 载荷 → session ref（下游输入交接）。"""
        data = payload.get("features")
        if data is None or not session_id:
            return None
        try:
            from app.services.geocompute._async_bridge import run_coro_sync
            from app.services.session_data import session_data_manager

            return run_coro_sync(session_data_manager.store(
                session_id, data,
                prefix=f"geocompute-part-{node.semantic_fingerprint()}",
            ))
        except Exception:  # noqa: BLE001 - 落存失败 → 无 ref（诚实降级）
            return None

