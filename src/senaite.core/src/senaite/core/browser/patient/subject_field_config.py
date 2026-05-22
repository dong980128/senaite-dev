# -*- coding: utf-8 -*-

# 配置每个实验默认字段
ANALYSIS_DEFAULT_FIELDS = {
    "HLA-DNA-typing-生信分析": [
        "result_kshId",
        "result_tumor",
        "result_patientID",
        "result_hla_a",
        "result_hla_b",
        "result_hla_c",
    ],
    # 其他实验如果有需求，追加(同上)
}