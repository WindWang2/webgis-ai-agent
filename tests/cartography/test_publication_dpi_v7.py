"""V7（Goal 08 Phase H）publication DPI 可选化契约测试.

publication 链此前的渲染 DPI 是常量 300；本分支将其参数化（72-600，
越界钳制 + 生效值披露），缺省路径 byte 一致。WeasyPrint 缺席的环境
（如本机）只测纯函数钳制面与请求契约，渲染集成由既有 publication
测试在可用环境覆盖。
"""
import pytest

from app.services.publication_export import (
    DPI_DEFAULT,
    DPI_MAX,
    DPI_MIN,
    clamp_target_dpi,
)


class TestTargetDpiContract:
    def test_bounds_constants(self):
        assert DPI_MIN == 72 and DPI_MAX == 600 and DPI_DEFAULT == 300

    def test_valid_values_pass_through(self):
        assert clamp_target_dpi(300) == 300
        assert clamp_target_dpi(150) == 150
        assert clamp_target_dpi(600) == 600
        assert clamp_target_dpi(72) == 72

    def test_out_of_bounds_clamped(self):
        assert clamp_target_dpi(10) == DPI_MIN
        assert clamp_target_dpi(1200) == DPI_MAX

    def test_invalid_values_fall_back_to_default(self):
        assert clamp_target_dpi(None) == DPI_DEFAULT
        assert clamp_target_dpi("abc") == DPI_DEFAULT
        assert clamp_target_dpi(float("nan")) == DPI_DEFAULT
        assert clamp_target_dpi(300.9) == 300
        # 请求契约（VectorPdfRequest.target_dpi Optional[int] = None）由
        # test_vector_pdf_route 在可用平台覆盖（route 模块导入链在
        # Windows 缺 fcntl，无法本机导入 —— 既有环境限制）。
