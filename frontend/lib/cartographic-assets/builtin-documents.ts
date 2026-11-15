/**
 * G40 内置符号资产规范形（**生成物** —— 由 app/lib/cartography/symbol_asset.py
 * 的 BUILTIN_SYMBOL_ASSETS 单源生成（exclude_none）；勿手改。再生成：
 *   .venv/bin/python - <<'EOF'
 *   import json; from app.lib.cartography.symbol_asset import BUILTIN_SYMBOL_ASSETS
 *   print(json.dumps({k: d.model_dump(exclude_none=True) for k, d in BUILTIN_SYMBOL_ASSETS.items()}, ensure_ascii=False, indent=2))
 *   EOF
 * 漂移由 tests/cartography/test_g40_symbol_assets.py::test_builtin_ts_twin_no_drift 锁定。
 */
import { SymbolAssetDocument } from './contracts';

export const BUILTIN_DOCUMENTS: Record<string, SymbolAssetDocument> = {
  "cartoasset.builtin.triangle": {
    "schema_version": 1,
    "kind": "point_symbol",
    "name": "三角形",
    "viewbox": [
      24.0,
      24.0
    ],
    "primitives": [
      {
        "op": "path",
        "fill": "#2563eb",
        "stroke": "none",
        "stroke_width": 0.0,
        "opacity": 1.0,
        "d": [
          [
            "M",
            12.0,
            3.0
          ],
          [
            "L",
            22.0,
            20.0
          ],
          [
            "L",
            2.0,
            20.0
          ],
          [
            "Z"
          ]
        ]
      }
    ],
    "default_params": {
      "size_px": 16.0,
      "anchor": "center",
      "rotation_deg": 0.0
    },
    "source": {
      "origin": "builtin",
      "license": "repo",
      "note": "WebGIS 内置图元（G40）"
    },
    "content_digest": "sha256:c10b933edc9864302fb697f1fa43e01c0f9ec45f6ef7922cdeee120433d4a013"
  },
  "cartoasset.builtin.square": {
    "schema_version": 1,
    "kind": "point_symbol",
    "name": "方形",
    "viewbox": [
      24.0,
      24.0
    ],
    "primitives": [
      {
        "op": "path",
        "fill": "#16a34a",
        "stroke": "none",
        "stroke_width": 0.0,
        "opacity": 1.0,
        "d": [
          [
            "M",
            3.5,
            3.5
          ],
          [
            "L",
            20.5,
            3.5
          ],
          [
            "L",
            20.5,
            20.5
          ],
          [
            "L",
            3.5,
            20.5
          ],
          [
            "Z"
          ]
        ]
      }
    ],
    "default_params": {
      "size_px": 16.0,
      "anchor": "center",
      "rotation_deg": 0.0
    },
    "source": {
      "origin": "builtin",
      "license": "repo",
      "note": "WebGIS 内置图元（G40）"
    },
    "content_digest": "sha256:6539357a43f6f12dad335689f859a4fc5f15ddf1c55826112f079a3d55fa8ca1"
  },
  "cartoasset.builtin.diamond": {
    "schema_version": 1,
    "kind": "point_symbol",
    "name": "菱形",
    "viewbox": [
      24.0,
      24.0
    ],
    "primitives": [
      {
        "op": "path",
        "fill": "#d97706",
        "stroke": "none",
        "stroke_width": 0.0,
        "opacity": 1.0,
        "d": [
          [
            "M",
            12.0,
            2.0
          ],
          [
            "L",
            22.0,
            12.0
          ],
          [
            "L",
            12.0,
            22.0
          ],
          [
            "L",
            2.0,
            12.0
          ],
          [
            "Z"
          ]
        ]
      }
    ],
    "default_params": {
      "size_px": 16.0,
      "anchor": "center",
      "rotation_deg": 0.0
    },
    "source": {
      "origin": "builtin",
      "license": "repo",
      "note": "WebGIS 内置图元（G40）"
    },
    "content_digest": "sha256:c66cb19b7c68021758511f1e2c5e4226894be40575e590291953446b54c8aafa"
  },
  "cartoasset.builtin.star5": {
    "schema_version": 1,
    "kind": "point_symbol",
    "name": "五角星",
    "viewbox": [
      24.0,
      24.0
    ],
    "primitives": [
      {
        "op": "path",
        "fill": "#dc2626",
        "stroke": "none",
        "stroke_width": 0.0,
        "opacity": 1.0,
        "d": [
          [
            "M",
            12.0,
            2.0
          ],
          [
            "L",
            14.469,
            8.602
          ],
          [
            "L",
            21.511,
            8.91
          ],
          [
            "L",
            15.994,
            13.298
          ],
          [
            "L",
            17.878,
            20.09
          ],
          [
            "L",
            12.0,
            16.2
          ],
          [
            "L",
            6.122,
            20.09
          ],
          [
            "L",
            8.006,
            13.298
          ],
          [
            "L",
            2.489,
            8.91
          ],
          [
            "L",
            9.531,
            8.602
          ],
          [
            "Z"
          ]
        ]
      }
    ],
    "default_params": {
      "size_px": 16.0,
      "anchor": "center",
      "rotation_deg": 0.0
    },
    "source": {
      "origin": "builtin",
      "license": "repo",
      "note": "WebGIS 内置图元（G40）"
    },
    "content_digest": "sha256:01266d3970da1d8af5f53f37dc3401dfc20786bbc1a9e508b876923aceb53e2b"
  },
  "cartoasset.builtin.cross": {
    "schema_version": 1,
    "kind": "point_symbol",
    "name": "十字",
    "viewbox": [
      24.0,
      24.0
    ],
    "primitives": [
      {
        "op": "path",
        "fill": "#7c3aed",
        "stroke": "none",
        "stroke_width": 0.0,
        "opacity": 1.0,
        "d": [
          [
            "M",
            9.5,
            2.5
          ],
          [
            "L",
            14.5,
            2.5
          ],
          [
            "L",
            14.5,
            9.5
          ],
          [
            "L",
            21.5,
            9.5
          ],
          [
            "L",
            21.5,
            14.5
          ],
          [
            "L",
            14.5,
            14.5
          ],
          [
            "L",
            14.5,
            21.5
          ],
          [
            "L",
            9.5,
            21.5
          ],
          [
            "L",
            9.5,
            14.5
          ],
          [
            "L",
            2.5,
            14.5
          ],
          [
            "L",
            2.5,
            9.5
          ],
          [
            "L",
            9.5,
            9.5
          ],
          [
            "Z"
          ]
        ]
      }
    ],
    "default_params": {
      "size_px": 16.0,
      "anchor": "center",
      "rotation_deg": 0.0
    },
    "source": {
      "origin": "builtin",
      "license": "repo",
      "note": "WebGIS 内置图元（G40）"
    },
    "content_digest": "sha256:45a3142af8a9f4ec915aa8931832128e5de9e950120abdd5ded30add9c029b40"
  },
  "cartoasset.builtin.pin": {
    "schema_version": 1,
    "kind": "point_symbol",
    "name": "定位钉",
    "viewbox": [
      24.0,
      24.0
    ],
    "primitives": [
      {
        "op": "path",
        "fill": "#e11d48",
        "stroke": "none",
        "stroke_width": 0.0,
        "opacity": 1.0,
        "d": [
          [
            "M",
            12.0,
            22.0
          ],
          [
            "C",
            12.0,
            22.0,
            4.0,
            13.0,
            4.0,
            8.8
          ],
          [
            "C",
            4.0,
            4.8,
            7.6,
            1.8,
            12.0,
            1.8
          ],
          [
            "C",
            16.4,
            1.8,
            20.0,
            4.8,
            20.0,
            8.8
          ],
          [
            "C",
            20.0,
            13.0,
            12.0,
            22.0,
            12.0,
            22.0
          ],
          [
            "Z"
          ]
        ]
      }
    ],
    "default_params": {
      "size_px": 16.0,
      "anchor": "center",
      "rotation_deg": 0.0
    },
    "source": {
      "origin": "builtin",
      "license": "repo",
      "note": "WebGIS 内置图元（G40）"
    },
    "content_digest": "sha256:d311777bb719b3547de22a9dc94728ad4ecab4b34a37303ca741b59f59343ac6"
  },
  "cartoasset.builtin.hatch": {
    "schema_version": 1,
    "kind": "area_texture",
    "name": "斜线晕线",
    "primitives": [],
    "pattern": {
      "type": "hatch"
    },
    "default_params": {
      "spacing_px": 12.0,
      "angle_deg": 45.0,
      "stroke_width_px": 1.5,
      "fg_color": "#334155",
      "bg_color": "transparent"
    },
    "source": {
      "origin": "builtin",
      "license": "repo",
      "note": "WebGIS 内置单方向晕线（G40）"
    },
    "content_digest": "sha256:645040f6aab01e1cc43e9c3cf49fe9acecf078725dfbb0e1b4fea88fb96a36a4"
  },
  "cartoasset.builtin.cross_hatch": {
    "schema_version": 1,
    "kind": "area_texture",
    "name": "交叉晕线",
    "primitives": [],
    "pattern": {
      "type": "cross_hatch"
    },
    "default_params": {
      "spacing_px": 12.0,
      "angle_deg": 45.0,
      "stroke_width_px": 1.2,
      "fg_color": "#334155",
      "bg_color": "transparent"
    },
    "source": {
      "origin": "builtin",
      "license": "repo",
      "note": "WebGIS 内置交叉晕线（G40）"
    },
    "content_digest": "sha256:fbeb2993fd05982801955be65744791f93dc0081ad284c5224f12e6d95b1dc4d"
  },
  "cartoasset.builtin.dots": {
    "schema_version": 1,
    "kind": "area_texture",
    "name": "点阵",
    "primitives": [],
    "pattern": {
      "type": "dots"
    },
    "default_params": {
      "spacing_px": 12.0,
      "stroke_width_px": 2.4,
      "fg_color": "#334155",
      "bg_color": "transparent"
    },
    "source": {
      "origin": "builtin",
      "license": "repo",
      "note": "WebGIS 内置点阵纹理（G40）"
    },
    "content_digest": "sha256:12592cae1c5ae97aa6d1a7c912d007e2fc0d9ebfe74354f5c533dfc362dac4b6"
  }
};
