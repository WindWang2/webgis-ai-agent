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
        MethodReference(
            "wright1936",
            "Dasymetric mapping (ancillary-weighted areal interpolation)",
            "Wright, J. K. (1936). A Method of Mapping Densities of Rural "
            "Population: With an Application to the Isle of Man. "
            "Geographical Review, 31(3), 392–402.",
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
            "D-infinity flow direction / partitioned accumulation",
            "Tarboton, D. G. (1997). A New Method for the Determination of "
            "Flow Directions and Upslope Areas in Grid Digital Elevation "
            "Models. Water Resources Research, 33(2), 309–319.",
        ),
        MethodReference(
            "ocallaghan_mark1984",
            "D8 flow direction / drainage network extraction",
            "O'Callaghan, J. F., & Mark, D. M. (1984). The Extraction of "
            "Drainage Networks from Digital Elevation Data. Computer "
            "Vision, Graphics, and Image Processing, 28(3), 323–344.",
        ),
        MethodReference(
            "wang_robinson_white2000",
            "Viewshed (R3 algorithm, sector-based without sightlines)",
            "Wang, J., Robinson, G. J., & White, K. (2000). Generating "
            "Viewsheds without Using Sightlines. Photogrammetric "
            "Engineering & Remote Sensing, 66(1), 87–90.",
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
            "page1954",
            "CUSUM (cumulative sum) change-point statistic",
            "Page, E. S. (1954). Continuous Inspection Schemes. "
            "Biometrika, 41(1/2), 100–115.",
        ),
        MethodReference(
            "esri_eha",
            "Emerging Hot Spot Analysis taxonomy (17 categories + none)",
            "Esri (2024). Emerging Hot Spot Analysis—Space Time Pattern "
            "Mining tool reference (ArcGIS Pro documentation). "
            "https://pro.arcgis.com/en/pro-app/latest/tool-reference/"
            "space-time-pattern-mining/emerginghotspots.htm",
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
        # ── 地形/成本面（Goal 07 Science V6）──────────────────────────
        MethodReference(
            "tobler1993",
            "Friction surface / cost distance (geographic movement)",
            "Tobler, W. (1993). Three Presentations on Geographical Analysis "
            "and Modeling: Non-Isotropic Geographic Modeling; Speculations on "
            "the Geometry of Geography; Global Spatial Analysis. "
            "NCGIA Technical Report 93-1.",
        ),
        # ── 生态（Goal 07 Science V6）───────────────────────────────
        MethodReference(
            "usfws1981",
            "Habitat Suitability Index (HSI) procedures",
            "USFWS. (1981). Standards for the Development of Habitat "
            "Suitability Index Models. U.S. Fish and Wildlife Service, "
            "Division of Ecological Services, ESM 103.",
        ),
        MethodReference(
            "mcgarigal_marks1995",
            "FRAGSTATS landscape pattern metrics",
            "McGarigal, K., & Marks, B. J. (1995). FRAGSTATS: Spatial "
            "Pattern Analysis Program for Quantifying Landscape Structure. "
            "Gen. Tech. Rep. PNW-GTR-351, USDA Forest Service.",
        ),
        # ── 空间抽样（Goal 07 Science V6）────────────────────────────
        MethodReference(
            "cochran1977",
            "Sampling design (simple random / systematic / stratified)",
            "Cochran, W. G. (1977). Sampling Techniques (3rd ed.). "
            "John Wiley & Sons.",
        ),
        MethodReference(
            "teitz_bart1968",
            "Teitz-Bart p-median heuristic",
            "Teitz, M. B., & Bart, P. (1968). Heuristic Methods for Estimating "
            "the Generalized Vertex Median of a Weighted Graph. "
            "Operations Research, 16(5), 955–961.",
        ),
        MethodReference(
            # science-v3 审计 R1（04 域 §7/§8）：venue/卷期勘误 —— 原
            # "IJHG, 11(1), 68–84" 有误；E2SFCA 发表于 Health & Place
            # 15(4):1100–1107（PubMed 19576837，已 web 复核）。
            "luo_qi2009",
            "2SFCA / E2SFCA accessibility",
            "Luo, W., & Qi, Y. (2009). An Enhanced Two-Step Floating Catchment "
            "Area (E2SFCA) Method for Measuring Spatial Accessibility to "
            "Primary Care Physicians. Health & Place, 15(4), 1100–1107.",
        ),
        MethodReference(
            # science-v3 审计 R1：2SFCA 原始出处（floating catchment + gravity
            # 两法统一框架），此前未登记 —— network.accessibility 方法谱系源头。
            "luo_wang2003",
            "2SFCA two-step floating catchment area (original)",
            "Luo, W., & Wang, F. (2003). Measures of Spatial Accessibility to "
            "Health Care in a GIS Environment: Synthesis and a Case Study in "
            "the Chicago Region. Environment and Planning B: Planning and "
            "Design, 30(6), 865–884.",
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
            "heinz_chang2001",
            "FCLS fully constrained linear spectral unmixing",
            "Heinz, D. C., & Chang, C.-I. (2001). Fully Constrained Least "
            "Squares Linear Spectral Mixture Analysis Method for Material "
            "Quantification in Hyperspectral Imagery. "
            "IEEE TGRS, 39(3), 529–545.",
        ),
        MethodReference(
            "flood2013",
            "Medoid temporal compositing (multi-dimensional median)",
            "Flood, N. (2013). Seasonal Composite Landsat TM/ETM+ Images "
            "Using the Medoid (a Multi-Dimensional Median). "
            "Remote Sensing, 5(12), 6481–6500.",
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
        # ── 空间统计 V2（Foundation V2 · shared A1）──────────────────
        MethodReference(
            "cliff_ord1973",
            "Join count statistics / spatial autocorrelation theory",
            "Cliff, A. D., & Ord, J. K. (1973). Spatial Autocorrelation. "
            "Pion, London.",
        ),
        MethodReference(
            "wartenberg1985",
            "Bivariate Moran's I",
            "Wartenberg, D. (1985). Multivariate Spatial Correlation: A "
            "Method for Exploratory Geographical Analysis. Geographical "
            "Analysis, 17(4), 263–283.",
        ),
        MethodReference(
            "wang2010",
            "Geographical detector (q-statistic)",
            "Wang, J.-F., Li, X.-H., Christakos, G., Liao, Y.-L., Zhang, T., "
            "Gu, X., & Zheng, X.-Y. (2010). Geographical Detectors-Based "
            "Health Risk Assessment and Its Application in the Neural Tube "
            "Defects Study of the Heshun Region, China. IJGIS, 24(1), 107–128.",
        ),
        MethodReference(
            "anselin1988",
            "Spatial econometrics (LM-lag / LM-error diagnostics, ML SAR/SEM)",
            "Anselin, L. (1988). Spatial Econometrics: Methods and Models. "
            "Kluwer Academic Publishers.",
        ),
        MethodReference(
            "ord1975",
            "ML estimation of spatial lag/error models (eigen log-Jacobian)",
            "Ord, J. K. (1975). Estimation Methods for Models of Spatial "
            "Interaction. JASA, 70(349), 120–126.",
        ),
        MethodReference(
            "brunsdon1996",
            "Geographically weighted regression",
            "Brunsdon, C., Fotheringham, A. S., & Charlton, M. E. (1996). "
            "Geographically Weighted Regression: A Method for Exploring "
            "Spatial Nonstationarity. Geographical Analysis, 28(4), 281–298.",
        ),
        MethodReference(
            "fotheringham2002",
            "GWR bandwidth selection / AICc",
            "Fotheringham, A. S., Brunsdon, C., & Charlton, M. (2002). "
            "Geographically Weighted Regression: The Analysis of Spatially "
            "Varying Relationships. Wiley.",
        ),
        MethodReference(
            "jarque_bera1980",
            "Jarque-Bera normality test (OLS residual diagnostics)",
            "Jarque, C. M., & Bera, A. K. (1980). Efficient Tests for "
            "Normality, Homoscedasticity and Serial Independence of "
            "Regression Residuals. Economics Letters, 6(3), 255–259.",
        ),
        MethodReference(
            "breusch_pagan1979",
            "Breusch-Pagan heteroscedasticity test",
            "Breusch, T. S., & Pagan, A. R. (1979). A Simple Test for "
            "Heteroscedasticity and Random Coefficient Variation. "
            "Econometrica, 47(5), 1287–1294.",
        ),
        MethodReference(
            "holm1979",
            "Holm step-down multiple testing correction",
            "Holm, S. (1979). A Simple Sequentially Rejective Multiple Test "
            "Procedure. Scandinavian Journal of Statistics, 6(2), 65–70.",
        ),
        # ── 点格局 V2（shared A3）────────────────────────────────────
        MethodReference(
            "diggle1983",
            "G / F nearest-neighbour distance functions",
            "Diggle, P. J. (1983). Statistical Analysis of Spatial Point "
            "Patterns. Academic Press.",
        ),
        MethodReference(
            "van_lieshout_baddeley1996",
            "J function (spatial interaction measure)",
            "van Lieshout, M.-C. N. M., & Baddeley, A. J. (1996). A "
            "Nonparametric Measure of Spatial Interaction in Point Patterns. "
            "Statistica Neerlandica, 50(3), 344–361.",
        ),
        MethodReference(
            "illian2008",
            "Pair correlation function / summary statistics",
            "Illian, J., Penttinen, A., Stoyan, H., & Stoyan, D. (2008). "
            "Statistical Analysis and Modelling of Spatial Point Patterns. "
            "Wiley.",
        ),
        MethodReference(
            "besag1977",
            "Cross-K under random labelling",
            "Besag, J. (1977). Contribution to the Discussion of Dr Ripley's "
            "Paper. JRSS-B, 39(2), 193–195.",
        ),
        MethodReference(
            "knox1964",
            "Knox space-time interaction test",
            "Knox, G. (1964). The Detection of Space-Time Interactions. "
            "Journal of the Royal Statistical Society: Series C (Applied "
            "Statistics), 13(1), 25–30.",
        ),
        # ── 插值 V2（shared A2）──────────────────────────────────────
        MethodReference(
            "matern1986",
            "Matérn covariance family",
            "Matérn, B. (1986). Spatial Variation (2nd ed.). Lecture Notes "
            "in Statistics 36. Springer.",
        ),
        MethodReference(
            "webster_oliver2007",
            "Variogram fitting / anisotropy / geostatistical practice",
            "Webster, R., & Oliver, M. A. (2007). Geostatistics for "
            "Environmental Scientists (2nd ed.). Wiley.",
        ),
        # science-v3 审计 02 域 §8 建议 #4：robust variogram 估计器出处。
        # empirical_variogram(robust=True) 的 0.457/0.494/0.045 修正常数
        # 即出自该文（2γ(h) = [mean|Δz|^½]⁴ / (0.457 + 0.494/|N(h)| +
        # 0.045/|N(h)|²)，对离群对稳健）。
        MethodReference(
            "cressie_hawkins1980",
            "Cressie–Hawkins robust semivariogram estimator",
            "Cressie, N., & Hawkins, D. M. (1980). Robust Estimation of "
            "the Variogram: I. Journal of the International Association "
            "for Mathematical Geology, 12(2), 115–125.",
        ),
        MethodReference(
            "odeh1995",
            "Regression kriging",
            "Odeh, I. O. A., McBratney, A. B., & Chittleborough, D. J. "
            "(1995). Further Results on Prediction of Soil Properties from "
            "Terrain Attributes: Heterotopic Cokriging and Regression-Kriging. "
            "Geoderma, 67(3-4), 215–226.",
        ),
        MethodReference(
            "watson1981",
            "Delaunay triangulation interpolation",
            "Watson, D. F. (1981). Computing the n-dimensional Delaunay "
            "Tessellation with Application to Voronoi Polytopes. "
            "The Computer Journal, 24(2), 167–172.",
        ),
        MethodReference(
            "clough_tocher1966",
            "C1 cubic triangulated interpolation (Clough-Tocher)",
            "Clough, R. W., & Tocher, J. L. (1966). Finite Element "
            "Stiffness Matrices for Analysis of Plates in Bending. "
            "Proc. 1st Conf. Matrix Methods in Structural Mechanics, 515–545.",
        ),
        # ── 网络 V2（shared A4）──────────────────────────────────────
        MethodReference(
            "hakimi1964",
            "Absolute centers / p-center problem",
            "Hakimi, S. L. (1964). Optimum Locations of Switching Centers "
            "and the Absolute Centers and Medians of a Graph. Operations "
            "Research, 12(3), 450–459.",
        ),
        MethodReference(
            # science-v3 审计 R0（04 域 F1/§7）：p-median MILP 的标准出处。
            # 题录经 web 复核（Wiley DOI 10.1111/j.1538-4632.1970.tb00142.x、
            # NASA ADS 1970GeoAn...2...30R）：篇名 Central Facilities Location，
            # Geographical Analysis 2(1), 30–42 —— 任务草案里的
            # "Integer Programming Formulations..., 2(4), 317–328" 有误，
            # 以出版方记录为准。ReVelle & Swain 首次把 p-median 写成整数
            # 规划式，是 pmedian_exact 的方法学锚点（church_revelle1974
            # 归还 MCLP/network.mclp_exact）。
            "revelle_swain1970",
            "p-median integer programming formulation",
            "ReVelle, C. S., & Swain, R. W. (1970). Central Facilities "
            "Location. Geographical Analysis, 2(1), 30–42.",
        ),
        MethodReference(
            "church_revelle1974",
            "Maximal covering location problem (MCLP)",
            "Church, R., & ReVelle, C. (1974). The Maximal Covering "
            "Location Problem. Papers of the Regional Science Association, "
            "32(1), 101–118.",
        ),
        MethodReference(
            "huff1964",
            "Huff spatial interaction / trade area model",
            "Huff, D. L. (1964). Defining and Estimating a Trading Area. "
            "Journal of Marketing, 28(3), 34–38.",
        ),
        MethodReference(
            "zipf1946",
            "Gravity model (P1·P2/D inverse-distance interaction)",
            "Zipf, G. K. (1946). The P1 P2/D Hypothesis: On the Intercity "
            "Movement of Persons. American Sociological Review, 11(6), 677–686.",
        ),
        MethodReference(
            "hansen1959",
            "Accessibility potential (gravity-type)",
            "Hansen, W. G. (1959). How Accessibility Shapes Land Use. "
            "Journal of the American Institute of Planners, 25(2), 73–76.",
        ),
        MethodReference(
            "brandes2001",
            "Betweenness centrality (Brandes' algorithm)",
            "Brandes, U. (2001). A Faster Algorithm for Betweenness "
            "Centrality. Journal of Mathematical Sociology, 25(2), 163–177.",
        ),
        # ── 地形 V2（shared A5）──────────────────────────────────────
        MethodReference(
            "barnes2014",
            "Priority-Flood depression filling",
            "Barnes, R., Lehman, C., & Mulla, D. (2014). Priority-Flood: An "
            "Optimal Depression-Filling and Watershed-Labeling Algorithm for "
            "Digital Elevation Models. Computers & Geosciences, 62, 117–127.",
        ),
        MethodReference(
            "strahler1957",
            "Strahler stream order / watershed morphometry",
            "Strahler, A. N. (1957). Quantitative Analysis of Watershed "
            "Geomorphology. Transactions, American Geophysical Union, "
            "38(6), 913–920.",
        ),
        MethodReference(
            "beven_kirkby1979",
            "Topographic Wetness Index (TWI)",
            "Beven, K. J., & Kirkby, M. J. (1979). A Physically Based, "
            "Variable Contributing Area Model of Basin Hydrology. "
            "Hydrological Bulletin, 23(1), 43–69.",
        ),
        MethodReference(
            "wischmeier_smith1978",
            "USLE LS factor",
            "Wischmeier, W. H., & Smith, D. D. (1978). Predicting Rainfall "
            "Erosion Losses: A Guide to Conservation Planning. USDA "
            "Agriculture Handbook 537.",
        ),
        MethodReference(
            "desmet_govers1996",
            "Grid-based USLE LS factor",
            "Desmet, P. J. J., & Govers, G. (1996). A GIS Procedure for "
            "Automatically Calculating the USLE LS Factor on Grid Cells. "
            "Journal of Soil and Water Conservation, 51(5), 427–433.",
        ),
        MethodReference(
            "yokoyama2002",
            "Terrain openness (positive/negative)",
            "Yokoyama, R., Shirasawa, M., & Pike, R. J. (2002). Visualizing "
            "Topography by Openness: A New Approach to Quantifying Visual "
            "Significance of Terrain. PE&RS, 68(3), 257–265.",
        ),
        MethodReference(
            "jasiewicz_stepinski2013",
            "Geomorphons landform pattern classification",
            "Jasiewicz, J., & Stepinski, T. F. (2013). Geomorphons — A "
            "Pattern Recognition Approach to Classification and Mapping of "
            "Landforms. Geomorphology, 182-183, 147–156.",
        ),
        # ── 遥感 / SAR V2（shared A6）────────────────────────────────
        MethodReference(
            "lee1980",
            "Lee speckle filter (local statistics)",
            "Lee, J.-S. (1980). Digital Image Enhancement and Noise "
            "Filtering by Use of Local Statistics. IEEE TPAMI, 2(2), 165–168.",
        ),
        MethodReference(
            "lee1981",
            "Refined Lee speckle filter (edge-directed)",
            "Lee, J.-S. (1981). Refined Filtering of Image Noise Using Local "
            "Statistics. Computer Graphics and Image Processing, 15(4), 380–389.",
        ),
        MethodReference(
            "lopes1990",
            "Adaptive speckle filters and scene heterogeneity (MAP/Refined-Lee)",
            "Lopes, A., Touzi, R., & Nezry, E. (1990). Adaptive Speckle "
            "Filters and Scene Heterogeneity. IEEE Trans. Geoscience and "
            "Remote Sensing, 28(6), 992–1000.",
        ),
        MethodReference(
            "frost1982",
            "Frost speckle filter",
            "Frost, V. S., Stiles, J. A., Shanmugan, K. S., & Holtzman, "
            "J. C. (1982). A Model for Radar Images and Its Application to "
            "Adaptive Digital Filtering of Multiplicative Noise. IEEE TPAMI, "
            "4(2), 157–166.",
        ),
        MethodReference(
            "oliver_quegan1998",
            "SAR statistics / radiometric calibration semantics",
            "Oliver, C., & Quegan, S. (1998). Understanding Synthetic "
            "Aperture Radar Images. Artech House.",
        ),
        MethodReference(
            "esa_s1_ipf_denoising",
            "Sentinel-1 GRD thermal noise denoising (IPF noise LUT semantics)",
            "European Space Agency (2017). Thermal Denoising of Products "
            "Generated by the Sentinel-1 IPF. S-1 Mission Performance Centre "
            "Technical Note, MPC-0392, Issue 1.1 (doc. ref. "
            "ESA-RS-CLI-52-0946), ESA.",
        ),
        MethodReference(
            "haralick1973",
            "GLCM texture features",
            "Haralick, R. M., Shanmugam, K., & Dinstein, I. (1973). Textural "
            "Features for Image Classification. IEEE Trans. Systems, Man, "
            "and Cybernetics, SMC-3(6), 610–621.",
        ),
        MethodReference(
            "crist_cicone1984",
            "Tasseled Cap (Landsat TM)",
            "Crist, E. P., & Cicone, R. C. (1984). A Physically-Based "
            "Transformation of Thematic Mapper Data—The Tasseled Cap. "
            "IEEE Trans. Geoscience and Remote Sensing, GE-22(3), 256–263.",
        ),
        MethodReference(
            "baig2014",
            "Tasseled Cap (Landsat 8 OLI)",
            "Baig, M. H. A., Zhang, L., Shuai, T., & Tong, Q. (2014). "
            "Derivation of a Tasselled Cap Transformation Based on Landsat 8 "
            "At-Satellite Reflectance. RSE, 140, 111–119.",
        ),
        MethodReference(
            "shi_xu2019",
            "Tasseled Cap (Sentinel-2)",
            "Shi, T., & Xu, H. (2019). Derivation of Tasseled Cap "
            "Transformation Coefficients for Sentinel-2 At-Satellite "
            "Reflectance. IEEE Geoscience and Remote Sensing Letters, "
            "16(1), 111–115.",
        ),
        # ── Foundation V3（spatial-algorithm-foundation-v3）────────────
        MethodReference(
            "fotheringham2017",
            "MGWR (multiscale GWR, backfitting)",
            "Fotheringham, A. S., Yang, W., & Kang, W. (2017). Multiscale "
            "Geographically Weighted Regression (MGWR). Annals of the "
            "American Association of Geographers, 107(6), 1247–1265.",
        ),
        MethodReference(
            "anselin_li2019",
            "Local join count (no-self-neighbor binary LISA)",
            "Anselin, L., & Li, X. (2019). A Local Join Count Approach to "
            "Count Data at the Local Level. Geographical Analysis, 51(2), "
            "244–268.",
        ),
        MethodReference(
            "sokal1998",
            "Local spatial autocorrelation (join count family)",
            "Sokal, R. R., Oden, N. L., & Thomson, B. A. (1998). Local "
            "Spatial Autocorrelation in a Biological Model. Geographical "
            "Analysis, 30(4), 331–354.",
        ),
        MethodReference(
            "journel_huijbregts1978",
            "Mining geostatistics (co-kriging / Markov Model 1)",
            "Journel, A. G., & Huijbregts, C. J. (1978). Mining "
            "Geostatistics. Academic Press.",
        ),
        MethodReference(
            "lindsay2016",
            "Depression breaching (selective breaching)",
            "Lindsay, J. B. (2016). The practice of DEM fluxation: "
            "depressions, breaching and fluvial modelling. Hydrological "
            "Processes, 30(4), 610-622. (Selective breaching variant.)",
        ),
        MethodReference(
            "renno2008",
            "HAND: Height Above the Nearest Drainage",
            "Rennó, C. D., Nobre, A. D., Cuartas, L. A., et al. (2008). "
            "HAND, a new terrain descriptor using SRTM-DEM: mapping terra-"
            "firme rainforest environments in Amazonia. Remote Sensing of "
            "Environment, 112(9), 3469-3481.",
        ),
        MethodReference(
            "shreve1966",
            "Shreve stream magnitude",
            "Shreve, R. L. (1966). Statistical law of stream numbers. "
            "Journal of Geology, 74(1), 17-37. (Magnitude = number of "
            "headwater links upstream.)",
        ),
        MethodReference(
            "pfafstetter1989",
            "Pfafstetter basin coding",
            "Pfafstetter, O. (1989). Classification of hydrographic basins: "
            "coding methodology. (Unpublished manuscript, Brazilian "
            "National Department of Water Resources; widely reproduced, "
            "e.g. Verdin & Verdin 1999.)",
        ),
        MethodReference(
            "strahler1952",
            "Hypsometric (area-altitude) analysis",
            "Strahler, A. N. (1952). Hypsometric (area-altitude) analysis "
            "of erosional topography. Geological Society of America "
            "Bulletin, 63(11), 1117-1142.",
        ),
        MethodReference(
            "fao56",
            "FAO Irrigation and Drainage Paper 56 (extraterrestrial radiation)",
            "Allen, R. G., Pereira, L. S., Raes, D., & Smith, M. (1998). "
            "Crop evapotranspiration — Guidelines for computing crop water "
            "requirements. FAO Irrigation and Drainage Paper 56. (Eq. 21: "
            "extraterrestrial daily radiation.)",
        ),
        MethodReference(
            "cressie1999",
            "Spatio-temporal covariance modelling (product-sum)",
            "Cressie, N. & Huang, H.-C. (1999). Classes of nonseparable, "
            "spatio-temporal stationary covariance functions. Journal of "
            "the American Statistical Association, 94(448), 1330–1340.",
        ),
        MethodReference(
            "goovaerts1997",
            "Geostatistics for Natural Resources Evaluation (SGS: sequential Gaussian simulation)",
            "Goovaerts, P. (1997). Geostatistics for Natural Resources "
            "Evaluation. Oxford University Press. (Ch. 7: conditional "
            "simulation — sequential Gaussian algorithm.)",
        ),
        MethodReference(
            "journel1983",
            "Indicator kriging (nonparametric distribution estimation)",
            "Journel, A. G. (1983). Nonparametric Estimation of Spatial "
            "Distributions. Journal of the International Association for "
            "Mathematical Geology, 15(3), 445–468.",
        ),
        MethodReference(
            "sibson1981",
            "Natural neighbour interpolation (Sibson coordinates)",
            "Sibson, R. (1981). A Brief Description of Natural Neighbor "
            "Interpolation. In Interpolating Multivariate Data, Wiley, "
            "21–36.",
        ),
        MethodReference(
            "duchon1977",
            "Thin-plate spline (RBF thin-plate kernel)",
            "Duchon, J. (1977). Splines Minimizing Rotation-Invariant "
            "Semi-Norms in Sobolev Spaces. In Constructive Theory of "
            "Functions of Several Variables, Lecture Notes in Mathematics "
            "571, Springer, 85–100.",
        ),
        MethodReference(
            "thiessen1911",
            "Thiessen (nearest-neighbour / Voronoi) polygon interpolation",
            "Thiessen, A. H. (1911). Precipitation Averages for Large "
            "Areas. Monthly Weather Review, 39(7), 1082–1084.",
        ),
        MethodReference(
            "isaaks_srivastava1989",
            "Applied geostatistics (block kriging / variogram practice)",
            "Isaaks, E. H., & Srivastava, R. M. (1989). An Introduction to "
            "Applied Geostatistics. Oxford University Press.",
        ),
        MethodReference(
            "mantel1967",
            "Mantel test (space-time distance association)",
            "Mantel, N. (1967). The Detection of Disease Clustering and a "
            "Generalized Regression Approach. Cancer Research, 27(2), "
            "209–220.",
        ),
        MethodReference(
            "diggle1995",
            "Space-time K function",
            "Diggle, P. J., Chetwynd, A. G., Häggkvist, R., & Morris, S. "
            "(1995). Second-Order Analysis of Space-Time Clustering. "
            "Statistical Methods in Medical Research, 4(2), 124–136.",
        ),
        MethodReference(
            "ripley1988",
            "Edge corrections for point-process summaries",
            "Ripley, B. D. (1988). Statistical Inference for Spatial "
            "Processes. Cambridge University Press.",
        ),
        MethodReference(
            "bonacich1972",
            "Eigenvector centrality",
            "Bonacich, P. (1972). Factoring and Weighting Approaches to "
            "Status Scores and Clique Identification. Journal of "
            "Mathematical Sociology, 2(1), 113–120.",
        ),
        MethodReference(
            "steyn1980",
            "Sky view factor estimation",
            "Steyn, D. G. (1980). The Calculation of View Factors from "
            "Horizon Angle Data. Atmosphere-Ocean, 18(3), 203–207.",
        ),
        MethodReference(
            "green1988",
            "MNF transform (noise-whitened PCA)",
            "Green, A. A., Berman, M., Switzer, P., & Craig, M. D. (1988). "
            "A Transformation for Ordering Multispectral Data in Terms of "
            "Image Quality with Implications for Noise Removal. IEEE Trans. "
            "Geoscience and Remote Sensing, 26(1), 65–74.",
        ),
        MethodReference(
            "hyvarinen1999",
            "FastICA",
            "Hyvärinen, A. (1999). Fast and Robust Fixed-Point Algorithms "
            "for Independent Component Analysis. IEEE Trans. Neural "
            "Networks, 10(3), 626–634.",
        ),
        MethodReference(
            "kruse1993",
            "Spectral angle mapper",
            "Kruse, F. A., Lefkoff, A. B., Boardman, J. W., Heidebrecht, "
            "K. B., Shapiro, A. T., Barloon, P. J., & Goetz, A. F. H. "
            "(1993). The Spectral Image Processing System (SIPS)—"
            "Interactive Visualization and Analysis of Imaging "
            "Spectrometer Data. Remote Sensing of Environment, 44(2-3), "
            "145–163.",
        ),
        MethodReference(
            "chang2000",
            "Spectral information divergence",
            "Chang, C.-I. (2000). An Information-Theoretic Approach to "
            "Spectral Variability, Similarity, and Discrimination for "
            "Hyperspectral Image Analysis. IEEE Trans. Information Theory, "
            "46(5), 1927–1932.",
        ),
        MethodReference(
            "boardman1995",
            "Matched filter (spectral target detection)",
            "Boardman, J. W. (1995). Analysis of AVIRIS Data via Spectral "
            "Unmixing and Expert Systems. JPL AVIRIS Workshop.",
        ),
        MethodReference(
            "reed1990",
            "RX anomaly detector",
            "Reed, I. S., & Yu, X. (1990). Adaptive Multiple-Band CFAR "
            "Detection of an Optical Pattern with Unknown Spectral "
            "Distribution. IEEE Trans. Acoustics, Speech, and Signal "
            "Processing, 38(10), 1760–1770.",
        ),
        MethodReference(
            "nielsen1998",
            "MAD / IR-MAD change detection",
            "Nielsen, A. A., Conradsen, K., & Simpson, J. J. (1998). "
            "Multivariate Alteration Detection (MAD) and MAF Postprocessing "
            "in Multispectral, Bitemporal Image Data: New Approaches to "
            "Change Detection Studies. Remote Sensing of Environment, "
            "64(1), 1–19.",
        ),
        MethodReference(
            "nascimento2005",
            "Vertex component analysis (endmember extraction)",
            "Nascimento, J. M. P., & Dias, J. M. B. (2005). Vertex "
            "Component Analysis: A Fast Algorithm to Unmix Hyperspectral "
            "Data. IEEE Trans. Geoscience and Remote Sensing, 43(4), "
            "898–910.",
        ),
        MethodReference(
            "lloyd1982",
            "K-means (Lloyd) segmentation foundation",
            "Lloyd, S. P. (1982). Least Squares Quantization in PCM. IEEE "
            "Trans. Information Theory, 28(2), 129–137.",
        ),
        MethodReference(
            "small2011",
            "Radiometric terrain correction (gamma flattening)",
            "Small, D. (2011). Flattening Gamma: Radiometric Terrain "
            "Correction for SAR Imagery. IEEE Trans. Geoscience and Remote "
            "Sensing, 49(8), 3081–3093.",
        ),
        MethodReference(
            "kuan1985",
            "Kuan adaptive noise filter",
            "Kuan, D. T., Sawchuk, A. A., Strand, T. C., & Chavel, P. "
            "(1985). Adaptive Noise Smoothing Filter for Images with Signal-"
            "Dependent Noise. IEEE Trans. Pattern Analysis and Machine "
            "Intelligence, 7(2), 165–177.",
        ),
        MethodReference(
            "lee_jurkevich1994",
            "Multi-temporal SAR speckle filtering",
            "Lee, J.-S., & Jurkevich, I. (1994). Speckle Filtering of "
            "Synthetic Aperture Radar Images: A Review. Remote Sensing "
            "Reviews, 8(4), 313–340.",
        ),
        # ── Foundation V3（completeness batch）：稳健协方差 / EB 率平滑 /
        #    自适应带宽核密度 / 经典季节分解 ─────────────────────────────
        MethodReference(
            "mackinnon_white1985",
            "Heteroskedasticity-consistent covariance estimators (HC0/HC1/HC3)",
            "MacKinnon, J. G., & White, H. (1985). Some Heteroskedasticity-"
            "Consistent Covariance Matrix Estimators with Improved Finite "
            "Sample Properties. Journal of Econometrics, 29(3), 305–325.",
        ),
        MethodReference(
            "marshall1991",
            "Empirical Bayes rate smoothing (method-of-moments prior)",
            "Marshall, R. J. (1991). A Review of Methods for the Statistical "
            "Analysis of Spatial Patterns of Disease. JRSS-A, 154(3), 421–441.",
        ),
        MethodReference(
            "abramson1982",
            "Adaptive kernel bandwidth (square-root law)",
            "Abramson, I. S. (1982). On Bandwidth Variation in Kernel "
            "Estimates—A Square Root Law. The Annals of Statistics, 10(4), "
            "1217–1223.",
        ),
        MethodReference(
            "makridakis1998",
            "Classical time-series decomposition (centered MA trend + seasonal indices)",
            "Makridakis, S., Wheelwright, S. C., & Hyndman, R. J. (1998). "
            "Forecasting: Methods and Applications (3rd ed.). Wiley.",
        ),
    ]
}


def get_method_reference(ref_id: str) -> MethodReference | None:
    return METHOD_REFERENCES.get(ref_id)


def reference_exists(ref_id: str) -> bool:
    return ref_id in METHOD_REFERENCES
