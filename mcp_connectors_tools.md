# Claude Science — 已连接的 MCP 连接器、工具与描述

共 **24 个连接器**、合计 **234 个工具**。调用方式(在 repl 中):`host.mcp("<连接器名>", "<工具名>", **kwargs)`。

> **关于参考链接:** 链接指向每个连接器背后的**公共数据源官方站点**,便于查阅数据来源与字段文档。
> 它们**不是** MCP server 的实际 endpoint URL —— 后者由平台编排层持有,不暴露给 agent,也不在会话沙箱内;连接器实际配置在 **Customize → Connectors** 管理。
> 工具描述提取自各连接器自动生成的技能文档。

---

## 基因组学与注释

### `mcp-genomes` — Ensembl REST lookup/序列/同源/VEP、UCSC 基因组浏览器轨迹与保守性
**参考:** [Ensembl REST](https://rest.ensembl.org) · [UCSC Genome Browser](https://genome.ucsc.edu)

| 工具 | 描述 |
|---|---|
| `ensembl_lookup` | 按ID或符号查询Ensembl基因/转录本/蛋白注释 |
| `ensembl_xrefs` | 获取Ensembl稳定ID的外部数据库交叉引用 |
| `ensembl_vep_variant` | 用VEP预测变异后果并按严重度汇总 |
| `ensembl_homology` | 从Compara获取基因的同源/旁系同源基因 |
| `ensembl_sequence` | 按稳定ID或基因组区域获取序列 |
| `ensembl_overlap_region` | 列出与基因组区域重叠的Ensembl特征 |
| `ucsc_list_tracks` | 列出UCSC某组装版本可查询的数据轨道 |
| `ucsc_track_data` | 获取UCSC任意轨道在区域内的原始行数据 |
| `ucsc_conservation` | 从UCSC phyloP/phastCons汇总区域保守性 |
| `ucsc_tfbs_clusters` | 获取区域内ENCODE转录因子结合位点簇 |
| `ucsc_chrom_sizes` | 获取UCSC组装的染色体/重叠群名称与大小 |

### `mcp-biomart` — Ensembl BioMart 基因组注释、ID 转换、交叉引用
**参考:** [Ensembl BioMart](https://www.ensembl.org/biomart/martview) · [BioMart service](https://www.ensembl.org/biomart/martservice)

| 工具 | 描述 |
|---|---|
| `list_marts` | 列出Ensembl BioMart所有可用的数据库(mart) |
| `list_datasets` | 列出指定mart下所有可用的数据集 |
| `list_common_attributes` | 列出数据集常用的属性字段 |
| `list_all_attributes` | 列出数据集全部(经过滤)属性字段 |
| `list_filters` | 列出数据集可用的查询过滤条件 |
| `get_data` | 按指定属性和过滤条件查询BioMart数据 |
| `get_translation` | 将单个标识符从一种类型转换为另一种 |
| `batch_translate` | 批量将多个标识符转换为目标类型 |

### `mcp-genes-ontologies` — MyGene、OLS4 本体、GO 注释(QuickGO)、UniProt、Reactome
**参考:** [MyGene.info](https://mygene.info) · [EBI OLS4](https://www.ebi.ac.uk/ols4) · [QuickGO](https://www.ebi.ac.uk/QuickGO) · [UniProt](https://www.uniprot.org) · [Reactome](https://reactome.org)

| 工具 | 描述 |
|---|---|
| `query_genes` | 通过mygene.info批量解析基因标识符/符号 |
| `list_ontologies` | 列出OLS4本体库目录或指定本体元数据 |
| `search_ontology_terms` | 按标签/同义词跨OLS4本体搜索术语 |
| `get_ontology_term` | 获取本体术语详情或其完整关联术语集 |
| `get_go_annotations` | 从QuickGO获取UniProt产物的GO注释 |
| `get_uniprot_entries` | 批量获取UniProtKB记录（字段/FASTA/文本） |
| `map_reactome_pathways` | 将基因/UniProt映射到Reactome通路 |

### `mcp-regulation` — ENCODE 实验/文件、JASPAR TF 矩阵、UniBind TFBS
**参考:** [ENCODE](https://www.encodeproject.org) · [JASPAR](https://jaspar.elixir.no) · [UniBind](https://unibind.uio.no)

| 工具 | 描述 |
|---|---|
| `encode_search_experiments` | 搜索ENCODE功能基因组学实验 |
| `encode_search_biosamples` | 搜索ENCODE生物样本(细胞系、组织等) |
| `encode_list_files` | 按格式/试验/样本列出ENCODE数据文件 |
| `encode_get_experiment` | 按编号获取单个ENCODE实验元数据 |
| `encode_get_file` | 按编号获取单个ENCODE文件元数据 |
| `encode_get_biosample` | 按编号获取单个ENCODE生物样本元数据 |
| `jaspar_get_matrix` | 按版本编号获取JASPAR转录因子结合谱 |
| `jaspar_matrix_versions` | 列出某JASPAR基础矩阵的所有版本 |
| `jaspar_list_matrices` | 搜索/列出JASPAR转录因子结合谱目录 |
| `jaspar_list_species` | 列出所有含JASPAR谱的物种 |
| `jaspar_list_taxa` | 列出所有JASPAR分类群 |
| `jaspar_list_collections` | 列出所有JASPAR集合 |
| `jaspar_list_releases` | 列出所有JASPAR数据库发布版本 |
| `unibind_search_tfbs` | 搜索UniBind高置信TFBS的ChIP-seq数据集 |
| `unibind_get_dataset` | 获取单个UniBind数据集详情及各模型TFBS |
| `unibind_tfbs_in_region` | 获取与基因组区间重叠的转录因子结合位点 |

### `mcp-expression` — GTEx 组织表达与 eQTL
**参考:** [GTEx Portal](https://gtexportal.org)

| 工具 | 描述 |
|---|---|
| `gtex_tissue_sites` | 列出GTEx所有组织位点及元数据 |
| `gtex_dataset_info` | 列出GTEx各数据集版本及元数据 |
| `gtex_sample_info` | 获取GTEx样本与供体元数据，可过滤 |
| `gtex_resolve_genes` | 将基因符号解析为带版本GENCODE ID |
| `gtex_median_expression` | 获取基因×组织的中位表达量（TPM） |
| `gtex_expression_summary` | 按中位TPM排名汇总基因跨组织表达 |
| `gtex_gene_expression` | 获取单基因各组织样本级TPM数组 |
| `gtex_top_expressed_genes` | 获取某组织中位TPM最高的前N基因 |
| `gtex_eqtl_genes` | 获取某组织所有显著cis-eQTL的eGene |
| `gtex_single_tissue_eqtls` | 获取基因或变异的显著单组织eQTL关联 |
| `gtex_multi_tissue_eqtls` | 获取基因的多组织eQTL荟萃分析结果 |
| `gtex_calculate_eqtl` | 为任意基因-变异对实时计算单组织eQTL |

### `mcp-rna` — 非编码 RNA — Rfam 家族、种子比对、协方差模型
**参考:** [Rfam](https://rfam.org)

| 工具 | 描述 |
|---|---|
| `get_family` | 获取Rfam家族元数据记录 |
| `get_seed_alignment` | 获取Rfam家族的种子比对 |
| `get_covariance_model` | 获取Rfam家族的Infernal协方差模型(CM) |
| `get_tree` | 获取Rfam家族的种子系统发育树 |
| `get_sequence_regions` | 获取Rfam家族在序列库中的全部区域命中 |
| `get_structure_mapping` | 获取Rfam家族的PDB残基级结构映射 |
| `accession_to_id` | 将Rfam编号转换为家族id |
| `id_to_accession` | 将Rfam家族id转换为编号 |
| `search_sequence` | 用cmscan将单条RNA序列比对所有Rfam协方差模型 |

---

## 人类遗传学与变异

### `mcp-variants` — gnomAD 频率/约束(r4)、ClinVar、dbSNP、结构/线粒体变异
**参考:** [gnomAD](https://gnomad.broadinstitute.org) · [ClinVar](https://www.ncbi.nlm.nih.gov/clinvar) · [dbSNP](https://www.ncbi.nlm.nih.gov/snp)

| 工具 | 描述 |
|---|---|
| `get_variant` | 按ID查询单个gnomAD短变异的人群频率 |
| `search_variants` | 在gnomAD中按rsID或变异ID搜索匹配的变异ID |
| `gene_variants` | 列出某基因内所有gnomAD短变异 |
| `gene_constraint` | 获取基因约束指标：pLI、观察/期望比、z分数 |
| `region_variants` | 列出某基因组区域内全部gnomAD短变异（≤1Mb） |
| `liftover_variant` | 在GRCh37与GRCh38参考版本间映射变异ID |
| `clinvar_variants` | 列出基因内ClinVar变异及临床意义和评级 |
| `structural_variants` | 列出与基因重叠的gnomAD结构变异 |
| `get_structural_variant` | 按SV ID查询单个gnomAD结构变异 |
| `mitochondrial_variants` | 列出线粒体基因或区域的异质性感知变异 |
| `clinvar_search` | 直连NCBI搜索ClinVar变异记录 |
| `clinvar_get_records` | 按VCV/RCV或变异ID批量获取完整ClinVar记录 |
| `clinvar_variant_by_rsid` | 按dbSNP rsID获取全部ClinVar变异记录 |
| `dbsnp_get_rsids` | 批量获取rsID的dbSNP RefSNP完整记录 |
| `dbsnp_search_by_region` | 列出某基因组窗口内的dbSNP rsID |

### `mcp-human-genetics` — GWAS Catalog、eQTL Catalogue、FinnGen/BBJ PheWAS
**参考:** [GWAS Catalog](https://www.ebi.ac.uk/gwas) · [eQTL Catalogue](https://www.ebi.ac.uk/eqtl) · [FinnGen](https://www.finngen.fi/en)

| 工具 | 描述 |
|---|---|
| `gwas_associations_for_variant` | 获取某变异（rsID）的GWAS Catalog关联 |
| `gwas_associations_for_gene` | 获取映射到某基因的GWAS Catalog关联 |
| `gwas_associations_for_trait` | 获取某EFO性状的GWAS Catalog关联 |
| `gwas_search_traits` | 按标签子串搜索GWAS Catalog性状本体ID |
| `gwas_search_studies` | 按性状注释或出版物搜索GWAS研究 |
| `gwas_get_study` | 按GCST登录号获取单个GWAS研究 |
| `gwas_get_variant` | 按rsID获取GWAS Catalog变异记录 |
| `eqtl_list_datasets` | 列出eQTL Catalogue数据集（研究×组织×方法） |
| `eqtl_associations` | 获取eQTL数据集按基因/变异/区域的关联行 |
| `phewas_instances` | 列出可查询的PheWAS门户及能力注册表 |
| `phewas_variant` | 获取某变异对所有表型的PheWAS关联统计 |
| `phewas_finngen_gene` | 获取FinnGen各疾病终点基因区最佳变异 |
| `phewas_list_phenotypes` | 获取PheWeb实例完整表型目录及病例数 |
| `phewas_search_phenotypes` | 按名称搜索PheWeb表型以解析phenocode |

### `mcp-clinical-genomics` — ClinGen、CIViC 临床证据、Open Targets
**参考:** [ClinGen](https://clinicalgenome.org) · [CIViC](https://civicdb.org) · [Open Targets](https://platform.opentargets.org)

| 工具 | 描述 |
|---|---|
| `clingen_gene_validity` | 查询ClinGen基因-疾病有效性判定 |
| `clingen_dosage_sensitivity` | 查询ClinGen基因剂量敏感性判定 |
| `clingen_actionability` | 查询ClinGen临床可干预性判定 |
| `clingen_variant_classifications` | 查询ClinGen专家组变异致病性分类 |
| `civic_search_genes` | 按Entrez符号查找CIViC基因记录 |
| `civic_gene_variants` | 获取某CIViC基因的所有变异 |
| `civic_get_variant` | 按ID获取单个CIViC变异详情 |
| `civic_search_variants` | 按名称子串搜索CIViC变异 |
| `civic_get_evidence_item` | 按ID获取单条CIViC临床证据 |
| `civic_search_evidence` | 按多种过滤条件搜索CIViC证据项 |
| `civic_get_assertion` | 按ID获取单条CIViC断言 |
| `civic_search_assertions` | 按多种过滤条件搜索CIViC断言 |
| `civic_get_molecular_profile` | 按ID获取CIViC分子谱 |
| `civic_search_molecular_profiles` | 按名称子串搜索CIViC分子谱 |
| `civic_search_diseases` | 按名称子串搜索CIViC疾病记录 |
| `civic_search_therapies` | 按名称子串搜索CIViC疗法记录 |
| `open_targets_graphql` | 对Open Targets运行任意GraphQL查询 |
| `open_targets_disease_drugs` | 查询某疾病的已知或在研药物 |
| `open_targets_disease_targets` | 查询某疾病关联度最高的靶点 |
| `open_targets_drug` | 按ChEMBL ID查询药物详情 |

---

## 临床、药物与监管

### `mcp-clinical-trials` — 临床试验检索、详情、赞助方、研究者、终点
**参考:** [ClinicalTrials.gov](https://clinicaltrials.gov)

| 工具 | 描述 |
|---|---|
| `search_trials` | 搜索ClinicalTrials.gov临床试验（主要工具） |
| `get_trial_details` | 按NCT ID获取某临床试验的完整详情 |
| `search_by_sponsor` | 查找某公司或机构赞助的所有临床试验 |
| `search_investigators` | 查找某治疗领域的主要研究者和研究点 |
| `analyze_endpoints` | 分析单个或多个试验的主次要终点指标 |
| `search_by_eligibility` | 按患者入组标准匹配查找临床试验 |

### `mcp-drug-regulatory` — FDA Drugs@FDA 申请/批准、药理分类、SPL 标签
**参考:** [Drugs@FDA](https://www.accessdata.fda.gov/scripts/cder/daf) · [openFDA](https://open.fda.gov)

| 工具 | 描述 |
|---|---|
| `search_drug_applications` | 按多字段搜索FDA药品申请(NDA/ANDA/BLA) |
| `get_drug_application` | 按申请号获取单个Drugs@FDA申请 |
| `count_drug_applications` | 按某字段对药品申请做分桶计数 |
| `get_drug_statistics` | 一次获取Drugs@FDA语料级统计 |
| `list_pharmacologic_classes` | 枚举Drugs@FDA中的药理学分类及计数 |
| `get_generic_equivalents` | 查找与某品牌药活性成分相同的仿制药 |
| `search_drug_labels` | 按成分/名称/途径检索FDA药品说明书 |

### `mcp-cancer-models` — cBioPortal 癌症基因组队列
**参考:** [cBioPortal](https://www.cbioportal.org)

| 工具 | 描述 |
|---|---|
| `cbioportal_list_studies` | 搜索并列出cBioPortal癌症基因组研究队列 |
| `cbioportal_get_study` | 获取单个研究的完整记录与分子数据概况 |
| `cbioportal_mutations_in_gene` | 查询某研究中某基因的所有体细胞突变 |
| `cbioportal_mutation_frequency` | 跨多个队列比较某基因的突变频率 |
| `cbioportal_cna_in_gene` | 查询某研究中某基因的拷贝数变异事件 |
| `cbioportal_clinical_attributes` | 列出研究的临床数据字典与属性 |

---

## 蛋白质与结构

### `mcp-protein-annotation` — InterPro/Pfam 结构域、Human Protein Atlas、STRING
**参考:** [InterPro](https://www.ebi.ac.uk/interpro) · [Human Protein Atlas](https://www.proteinatlas.org) · [STRING](https://string-db.org)

| 工具 | 描述 |
|---|---|
| `get_domain_architecture` | 获取UniProt蛋白的完整InterPro结构域架构 |
| `search_interpro_entries` | 关键词搜索InterPro或成员库条目 |
| `get_interpro_entry` | 获取InterPro条目或Pfam家族的详情记录 |
| `search_pfam_clans` | 关键词搜索Pfam clan族群 |
| `get_pfam_clan` | 获取Pfam clan详情及成员家族列表 |
| `get_pfam_family_proteins` | 获取Pfam家族的成员蛋白或计数 |
| `get_pfam_family_proteomes` | 获取含某Pfam家族成员的蛋白质组 |
| `get_protein_atlas_gene` | 获取人类蛋白质图谱的单基因记录 |
| `search_protein_atlas` | 按列检索人类蛋白质图谱 |
| `map_string_ids` | 将基因符号映射到STRING蛋白标识符 |
| `get_string_network` | 获取基因列表的STRING蛋白互作网络 |
| `get_string_similarity_scores` | 获取基因集内蛋白的STRING同源相似度分值 |
| `get_string_best_similarity_hits` | 获取各蛋白在目标物种的最佳同源命中 |

### `mcp-structures-interactions` — PDB、AlphaFold、EMDB、Complex Portal、IntAct
**参考:** [RCSB PDB](https://www.rcsb.org) · [AlphaFold DB](https://alphafold.ebi.ac.uk) · [EMDB](https://www.ebi.ac.uk/emdb) · [Complex Portal](https://www.ebi.ac.uk/complexportal) · [IntAct](https://www.ebi.ac.uk/intact)

| 工具 | 描述 |
|---|---|
| `emdb_get_entries` | 获取EMDB冷冻电镜条目的结构化元数据 |
| `emdb_search_entries` | 用Solr式查询搜索EMDB并分页检索 |
| `emdb_get_entry_section` | 获取EMDB条目的某个详细元数据分节 |
| `emdb_get_validation` | 获取EMDB条目的数值验证分析指标 |
| `complexportal_get_complexes` | 按CPX编号获取Complex Portal策展记录 |
| `complexportal_search_by_participant` | 搜索含某分子的Complex Portal复合物 |
| `intact_fetch_interactions` | 检索匹配查询的全部IntAct二元互作 |
| `intact_get_interactor` | 将分子解析为其IntAct互作体记录 |
| `intact_get_interaction_details` | 获取单个IntAct互作的完整策展详情 |
| `intact_build_network` | 围绕种子蛋白构建深度1的IntAct互作网络 |
| `pdb_search_structures` | 按属性过滤搜索RCSB PDB结构条目 |
| `pdb_get_structures` | 批量获取PDB条目的条目级摘要 |
| `pdb_get_entities` | 获取PDB条目的聚合物实体详情及UniProt映射 |
| `pdb_get_ligands` | 获取PDB条目结合的配体及化学信息 |
| `alphafold_get_prediction` | 获取单个UniProt的AlphaFold预测结构元数据 |
| `alphafold_check_coverage` | 批量检查UniProt的AlphaFold覆盖情况 |

### `mcp-cellguide` — CELLxGENE 细胞类型信息、标志基因
**参考:** [CELLxGENE CellGuide](https://cellxgene.cziscience.com/cellguide)

| 工具 | 描述 |
|---|---|
| `get_cell_type_info` | 获取某细胞类型的描述、同义词等基本信息 |
| `search_cell_types` | 按名称或同义词搜索细胞类型 |
| `get_marker_genes` | 获取某细胞类型的标志基因 |
| `get_source_data` | 获取某细胞类型的来源数据集与文献 |
| `get_cell_tissues` | 获取某细胞类型所分布的组织位置 |

---

## 化学与小分子

### `mcp-chemistry` — PubChem、ChEBI、Rhea 反应、BindingDB
**参考:** [PubChem](https://pubchem.ncbi.nlm.nih.gov) · [ChEBI](https://www.ebi.ac.uk/chebi) · [Rhea](https://www.rhea-db.org) · [BindingDB](https://www.bindingdb.org)

| 工具 | 描述 |
|---|---|
| `pubchem_search_compounds` | 将化学标识符解析为PubChem CID及属性 |
| `pubchem_get_compounds` | 批量获取PubChem CID的完整属性记录 |
| `pubchem_similarity_search` | 按SMILES做2D Tanimoto相似性检索 |
| `pubchem_get_bioassay_summary` | 获取某化合物的生物测定活性汇总 |
| `pubchem_get_safety` | 获取某化合物的GHS安全分类信息 |
| `chebi_search` | 全文检索ChEBI化学实体 |
| `chebi_get_entity` | 获取ChEBI实体的完整记录 |
| `chebi_get_ontology` | 获取ChEBI实体的本体关系(父子等) |
| `rhea_search_reactions` | 按方程、ChEBI或EC号搜索Rhea反应 |
| `rhea_get_reaction` | 获取单个Rhea反应的完整记录 |
| `bindingdb_ligands_by_target` | 按UniProt查询某靶点的配体结合亲和力 |
| `bindingdb_targets_by_compound` | 查询与某分子相似化合物所结合的靶点 |

### `mcp-chembl` — ChEMBL 化合物/生物活性/靶点/机制/ADMET
**参考:** [ChEMBL](https://www.ebi.ac.uk/chembl)

| 工具 | 描述 |
|---|---|
| `compound_search` | 按名称、ID或结构搜索ChEMBL化合物 |
| `get_bioactivity` | 获取化合物-靶点的生物活性测量（IC50、Ki等） |
| `target_search` | 搜索ChEMBL中的蛋白、酶、受体等靶点 |
| `get_mechanism` | 获取已批准药物及候选药的作用机制 |
| `drug_search` | 按治疗适应症搜索已批准药物及临床候选药 |
| `get_admet` | 获取化合物的ADMET相关计算分子性质 |

### `mcp-zinc` — ZINC22 可购买化学空间(CartBlanche22)
**参考:** [ZINC22 / CartBlanche](https://cartblanche.docking.org)

| 工具 | 描述 |
|---|---|
| `zinc_search_by_id` | 按ZINC ID查询可购买化合物及供应商 |
| `zinc_search_by_smiles` | 按SMILES做精确或相似结构搜索可购买化合物 |
| `zinc_search_by_supplier` | 将供应商目录号解析为ZINC化合物 |
| `zinc_random_sample` | 从ZINC22随机抽取可购买化合物样本 |
| `zinc_get_3d` | 定位ZINC化合物的对接就绪3D结构位置 |

### `mcp-ketcher-chemistry` — 交互式 2D 分子绘制器(Ketcher App)
**参考:** [Ketcher](https://lifescience.opensource.epam.com/ketcher)

| 工具 | 描述 |
|---|---|
| `open_sketcher` | 打开交互式2D分子绘图器并保存为构件 |

---

## 文献与科研资源

### `mcp-pubmed` — PubMed 文献检索、元数据、全文、版权
**参考:** [PubMed](https://pubmed.ncbi.nlm.nih.gov)

| 工具 | 描述 |
|---|---|
| `search_articles` | 按查询搜索PubMed生物医学文献 |
| `get_article_metadata` | 按PMID获取文章详细元数据 |
| `find_related_articles` | 查找PubMed相关文章及关联资源 |
| `lookup_article_by_citation` | 按引文信息匹配查找文章PMID |
| `convert_article_ids` | 在PMID、PMCID、DOI等ID格式间转换 |
| `get_full_text_article` | 从PMC获取全文文章 |
| `get_copyright_status` | 获取文章的版权与许可信息 |

### `mcp-literature` — OpenAlex 引文图、arXiv 元数据
**参考:** [OpenAlex](https://openalex.org) · [arXiv](https://arxiv.org)

| 工具 | 描述 |
|---|---|
| `openalex_search_works` | 带过滤条件搜索OpenAlex学术著作 |
| `openalex_get_work` | 获取单篇OpenAlex著作的完整信息 |
| `openalex_citations` | 列出引用某著作的作品（入向引用） |
| `openalex_references` | 列出某著作引用的参考文献（出向引用） |
| `openalex_search_authors` | 按姓名搜索OpenAlex作者档案 |
| `openalex_get_author` | 获取单个作者档案及高被引著作 |
| `openalex_venue_info` | 查询OpenAlex期刊/仓储来源信息与指标 |
| `arxiv_search` | 通过官方API搜索arXiv预印本 |
| `arxiv_get_papers` | 按ID批量获取arXiv论文元数据与摘要 |

### `mcp-biorxiv` — bioRxiv/medRxiv 预印本检索与统计
**参考:** [bioRxiv](https://www.biorxiv.org) · [medRxiv](https://www.medrxiv.org)

| 工具 | 描述 |
|---|---|
| `search_preprints` | 按日期和类别搜索bioRxiv/medRxiv预印本 |
| `get_preprint` | 按DOI获取某预印本的完整元数据 |
| `get_categories` | 列出全部27个bioRxiv学科类别 |
| `search_published_preprints` | 查找已在期刊正式发表的预印本 |
| `search_by_funder` | 按资助机构ROR ID搜索预印本 |
| `get_content_statistics` | 获取bioRxiv投稿量统计随时间变化 |
| `get_usage_statistics` | 获取bioRxiv阅读与下载使用统计 |

### `mcp-omics-archives` — ArrayExpress/BioStudies、GEO、MetaboLights、MGnify、PRIDE
**参考:** [BioStudies/ArrayExpress](https://www.ebi.ac.uk/biostudies) · [GEO](https://www.ncbi.nlm.nih.gov/geo) · [MetaboLights](https://www.ebi.ac.uk/metabolights) · [MGnify](https://www.ebi.ac.uk/metagenomics) · [PRIDE](https://www.ebi.ac.uk/pride)

| 工具 | 描述 |
|---|---|
| `arrayexpress_search_experiments` | 搜索ArrayExpress功能基因组学实验（完整验证检索） |
| `arrayexpress_get_experiment` | 获取单个ArrayExpress实验的扁平化元数据记录 |
| `arrayexpress_get_experiment_files` | 列出实验的所有文件及下载端点 |
| `arrayexpress_get_experiment_samples` | 获取实验的逐样本SDRF注释行 |
| `geo_search_series` | 搜索NCBI GEO数据集并返回系列级记录 |
| `geo_get_series` | 获取GEO系列(GSE)及其样本的结构化元数据 |
| `metabolights_list_studies` | 列出所有公开MetaboLights研究编号 |
| `metabolights_get_studies` | 获取MetaboLights研究的结构化元数据 |
| `metabolights_get_study_files` | 列出某MetaboLights研究的完整文件清单 |
| `metabolights_search_data_files` | 对研究原始数据文件夹做通配符搜索 |
| `mgnify_search_studies` | 按文本或生物群系谱系查找MGnify宏基因组研究 |
| `mgnify_get_studies` | 获取MGnify研究(MGYS)的结构化记录 |
| `mgnify_get_study_analyses` | 列出某MGnify研究的全部分析(完整分页) |
| `pride_search_projects` | 搜索PRIDE蛋白质组学项目(完整验证检索) |
| `pride_get_projects` | 按编号获取PRIDE项目的完整元数据 |
| `pride_search_project_proteins` | 列出某PRIDE亲和蛋白质组项目的蛋白证据行 |
| `pride_find_projects_for_protein` | 查找含指定蛋白的PRIDE项目 |

### `mcp-research-resources` — Grants.gov 经费、Antibody Registry 抗体
**参考:** [Grants.gov](https://www.grants.gov) · [Antibody Registry](https://www.antibodyregistry.org)

| 工具 | 描述 |
|---|---|
| `search_grants` | 搜索Grants.gov资助机会(完整验证检索) |
| `search_antibodies` | 全文搜索抗体注册库 |
| `get_antibody` | 按编号/RRID获取抗体注册库详情记录 |
| `find_antibodies_by_catalog` | 按厂商目录号精确查找抗体 |
| `get_antibody_registry_stats` | 获取抗体注册库整体统计信息 |

---
