"""Method References —— 规范方法出处登记（VNext §27）。

原则：

- 注册表只存 **concise id + 短引用**；全文/DOI/讨论放 docs/science/；
- descriptor 的 ``method_references`` 引用这里的 id，validate() 校验存在性
  —— 杜绝「声称科学权威却无处审计」；
- 只登记确有把握的经典出处；宁缺毋滥。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MethodReference:
    ref_id: str
    method_name: str
    citation: str


METHOD_REFERENCES: dict[str, MethodReference] = {
    ref.ref_id: ref
    for ref in [
        # ── 空间统计 ─────────────────────────────────────────────────
        MethodReference(
            "moran1950",
            "Moran's I (global spatial autocorrelation)",
            "Moran, P. A. P. (1950). Notes on Continuous Stochastic Phenomena. "
            "Biometrika, 37(1/2), 17–23.",
        ),
        MethodReference(
            "geary1954",
            "Geary's C (global spatial autocorrelation)",
            "Geary, R. C. (1954). The Contiguity Ratio and Statistical Mapping. "
            "The Incorporated Statistician, 5(3), 115–145.",
        ),
        MethodReference(
            "getis_ord1992",
            "Getis-Ord Gi* hotspot statistic",
            "Getis, A., & Ord, J. K. (1992). The Analysis of Spatial Association "
            "by Use of Distance Statistics. Geographical Analysis, 24(3), 189–206.",
        ),
        MethodReference(
            "ord_getis1995",
            "General G statistic",
            "Ord, J. K., & Getis, A. (1995). Local Spatial Autocorrelation "
            "Statistics: Distributional Issues and an Application. "
            "Geographical Analysis, 27(4), 286–306.",
        ),
        MethodReference(
            "anselin1995",
            "LISA (local indicators of spatial association)",
            "Anselin, L. (1995). Local Indicators of Spatial Association—LISA. "
            "Geographical Analysis, 27(2), 93–115.",
        ),
        MethodReference(
            "benjamini_hochberg1995",
            "Benjamini-Hochberg FDR correction",
            "Benjamini, Y., & Hochberg, Y. (1995). Controlling the False "
            "Discovery Rate. JRSS-B, 57(1), 289–300.",
        ),
        # ── 点格局 / 密度 ────────────────────────────────────────────
        MethodReference(
            "clark_evans1954",
            "Nearest neighbour index (NNI)",
            "Clark, P. J., & Evans, F. C. (1954). Distance to Nearest Neighbor "
            "as a Measure of Spatial Relationships in Populations. "
            "Ecology, 35(4), 445–453.",
        ),
        MethodReference(
            "ripley1976",
            "Ripley's K / L functions",
            "Ripley, B. D. (1976). The Second-Order Analysis of Stationary "
            "Point Processes. Journal of Applied Probability, 13(2), 255–266.",
        ),
        MethodReference(
            "silverman1986",
            "Kernel density estimation (bandwidth selection)",
            "Silverman, B. W. (1986). Density Estimation for Statistics and "
            "Data Analysis. Chapman & Hall.",
        ),
        MethodReference(
            "ester_kriegel1996",
            "DBSCAN clustering",
            "Ester, M., Kriegel, H.-P., Sander, J., & Xu, X. (1996). A Density-"
            "Based Algorithm for Discovering Clusters in Large Spatial "
            "Databases. KDD-96, 226–231.",
        ),
        # ── 插值 ─────────────────────────────────────────────────────
        MethodReference(
            "matheron1963",
            "Kriging / regionalized variable theory",
            "Matheron, G. (1963). Principles of Geostatistics. "
            "Economic Geology, 58(8), 1246–1266.",
        ),
        MethodReference(
            "shepard1968",
            "Inverse distance weighting",
            "Shepard, D. (1968). A Two-Dimensional Interpolation Function for "
            "Irregularly-Spaced Data. ACM-1968, 517–524.",
        ),
        # ── 地形 ─────────────────────────────────────────────────────
        MethodReference(
            "horn1981",
            "Horn 3×3 slope/aspect gradient",
            "Horn, B. K. P. (1981). Hill Shading and the Reflectance Map. "
            "Proceedings of the IEEE, 69(1), 14–47.",
        ),
        MethodReference(
            "zevenbergen_thorne1987",
            "Zevenbergen & Thorne curvature",
            "Zevenbergen, L. W., & Thorne, C. R. (1987). Quantitative Analysis "
            "of Land Surface Topography. Earth Surface Processes and "
            "Landforms, 12(1), 47–56.",
        ),
        MethodReference(
            "tarboton1997",
            "D8 flow direction / flow accumulation",
            "Tarboton, D. G. (1997). A New Method for the Determination of "
            "Flow Directions and Upslope Areas in Grid Digital Elevation "
            "Models. Water Resources Research, 33(2), 309–319.",
        ),
        MethodReference(
            "weiss2001",
            "TPI / slope position landform classes",
            "Weiss, A. D. (2001). Topographic Position and Landforms Analysis "
            "(conference poster). ESRI User Conference.",
        ),
        MethodReference(
            "wilson2007",
            "TRI / terrain roughness (bathymetric terrain analysis)",
            "Wilson, M. F. J., O'Connell, B., Brown, C., Guinan, J. C., & "
            "Grehan, A. J. (2007). Multiscale Terrain Analysis of "
            "Multibeam Bathymetry Data for Habitat Mapping. "
            "Int. J. Geographical Information Science, 21(9), 1021–1046.",
        ),
        # ── 时序 ─────────────────────────────────────────────────────
        MethodReference(
            "mann1945",
            "Mann-Kendall trend test",
            "Mann, H. B. (1945). Nonparametric Tests Against Trend. "
            "Econometrica, 13(3), 245–259.",
        ),
        MethodReference(
            "kendall1975",
            "Kendall's tau / trend significance",
            "Kendall, M. G. (1975). Rank Correlation Methods (4th ed.). "
            "Charles Griffin.",
        ),
        MethodReference(
            "sen1968",
            "Sen's slope estimator",
            "Sen, P. K. (1968). Estimates of the Regression Coefficient Based "
            "on Kendall's Tau. JASA, 63(324), 1379–1389.",
        ),
        MethodReference(
            "hirsch_slack1982",
            "Seasonal Mann-Kendall",
            "Hirsch, R. M., Slack, J. R., & Smith, R. A. (1982). Techniques of "
            "Trend Analysis for Monthly Water Quality Data. "
            "WRR, 18(1), 107–121.",
        ),
        # ── 网络 ─────────────────────────────────────────────────────
        MethodReference(
            "dijkstra1959",
            "Dijkstra shortest path",
            "Dijkstra, E. W. (1959). A Note on Two Problems in Connexion with "
            "Graphs. Numerische Mathematik, 1, 269–271.",
        ),
        MethodReference(
            "teitz_bart1968",
            "Teitz-Bart p-median heuristic",
            "Teitz, M. B., & Bart, P. (1968). Heuristic Methods for Estimating "
            "the Generalized Vertex Median of a Weighted Graph. "
            "Operations Research, 16(5), 955–961.",
        ),
        MethodReference(
            "luo_qi2009",
            "2SFCA / E2SFCA accessibility",
            "Luo, W., & Qi, Y. (2009). An Enhanced Two-Step Floating Catchment "
            "Area (E2SFCA) Method. IJHG, 11(1), 68–84.",
        ),
        # ── 遥感 ─────────────────────────────────────────────────────
        MethodReference(
            "rouse1974",
            "NDVI vegetation index",
            "Rouse, J. W., Haas, R. H., Schell, J. A., & Deering, D. W. (1974). "
            "Monitoring Vegetation Systems in the Great Plains with ERTS. "
            "NASA SP-351, 309–317.",
        ),
        MethodReference(
            "huete1988",
            "SAVI / EVI soil-adjusted indices",
            "Huete, A. R. (1988). A Soil-Adjusted Vegetation Index (SAVI). "
            "RSE, 25(3), 295–309.",
        ),
        MethodReference(
            "gao1996",
            "NDWI (vegetation water content)",
            "Gao, B.-C. (1996). NDWI—A Normalized Difference Water Index for "
            "Remote Sensing of Vegetation Liquid Water from Space. "
            "RSE, 58(3), 257–266.",
        ),
        MethodReference(
            "mcfeeters1996",
            "NDWI (open water / MNDWI lineage)",
            "McFeeters, S. K. (1996). The Use of the Normalized Difference "
            "Water Index (NDWI) in the Delineation of Open Water Features. "
            "IJRS, 17(7), 1425–1432.",
        ),
        MethodReference(
            "xu2006",
            "MNDWI water index",
            "Xu, H. (2006). Modification of Normalised Difference Water Index "
            "(NDWI) to Enhance Open Water Features in Remotely Sensed Images. "
            "IJRS, 27(14), 3025–3033.",
        ),
        MethodReference(
            "zha_woodcock2003",
            "NDBI built-up index",
            "Zha, Y., Gao, J., & Ni, S. (2003). Use of Normalized Difference "
            "Built-Up Index in Automatically Mapping Urban Areas from a "
            "TM Image. IJRS, 24(3), 583–594.",
        ),
        MethodReference(
            "key_benson2006",
            "NBR burn ratio (FireMON)",
            "Key, C. H., & Benson, N. C. (2006). Landscape Assessment: Ground "
            "Measure of Severity, the Composite Burn Index; and Remote Sensing "
            "of Severity, the Normalized Burn Ratio. FIREMON Landscape "
            "Assessment, RMRS-GTR-164-CD LA1-LA51.",
        ),
        # ── 变化检测 ─────────────────────────────────────────────────
        MethodReference(
            "malila1980",
            "Change vector analysis",
            "Malila, W. A. (1980). Change Vector Analysis: An Approach for "
            "Detecting Forest Changes with Landsat. LARS Symposia, 385–392.",
        ),
        # ── 决策 ─────────────────────────────────────────────────────
        MethodReference(
            "hwang_yoon1981",
            "TOPSIS MCDA",
            "Hwang, C. L., & Yoon, K. (1981). Multiple Attribute Decision "
            "Making: Methods and Applications. Springer.",
        ),
    ]
}


def get_method_reference(ref_id: str) -> MethodReference | None:
    return METHOD_REFERENCES.get(ref_id)


def reference_exists(ref_id: str) -> bool:
    return ref_id in METHOD_REFERENCES
