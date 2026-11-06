# 评分器使用说明

## 1. 安装环境

从 [Python 官网](https://www.python.org/downloads/) 安装 Python 3.10 或更新版本。Windows 安装时勾选将 Python 加入 PATH。下载并解压仓库，打开终端进入仓库根目录：

```powershell
cd ai-science-discovery-bench
python --version
```

Windows 若提示找不到 `python`，可以尝试 `py -3`，并在后续命令中将 `python` 换成 `py -3`。

**评分器只使用 Python 标准库，不需要运行任何 `pip install` 命令，也不需要 OpenAI SDK、PyTorch、CUDA 或本地模型。** 可选虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe --version
```

若不想激活虚拟环境，可直接用 `.\.venv\Scripts\python.exe` 替换下文的 `python`。

## 2. 配置模型接口

服务需要提供 `POST /v1/chat/completions` 或相同形式的 Chat Completions 接口。API 根地址要包含实际需要的前缀，例如 `/v1`，但不要包含 `/chat/completions`。

PowerShell：

```powershell
$env:ASD_BASE_URL = "https://your-model-service.example/v1"
$env:ASD_API_KEY = "YOUR_API_KEY"
$env:ASD_MODEL = "YOUR_MODEL_NAME"
```

macOS / Linux：

```bash
export ASD_BASE_URL="https://your-model-service.example/v1"
export ASD_API_KEY="YOUR_API_KEY"
export ASD_MODEL="YOUR_MODEL_NAME"
```

这些值都要替换成你自己的服务配置。无需鉴权的本地服务可将 `ASD_API_KEY` 留空。`.env.example` 是配置示意，程序不会自动加载 `.env` 文件。不要把真实密钥提交到 Git。

参考排行榜使用的模型名称为 Qwen3.6-27B。这里默认该名称只是为了记录历史设置，不表示本仓库提供模型，也不保证其他服务上同名模型的权重和部署方式相同。若换用其他模型，输出会记录新模型名称，不应直接混入参考排行榜。

## 3. 准备待评分问题

创建 UTF-8 编码的 JSONL 文件，一行一个最终科研问题。每行必须包含：

| 字段 | 含义 |
| --- | --- |
| `id` | 本次提交中唯一的问题 ID |
| `system` | 产生问题的系统名称，用于记录，不发送给评分模型 |
| `paper_id` | `领域/arXiv编号`，不带 `.pdf`；应与论文索引一致 |
| `material` | 需要评分的科学正文，不放内部自评分或历史评语 |

`material` 支持这些字段：

- `proposal`：完整科研问题或研究提案原文。
- `Title`、`Motivation`、`Experiment`：分别保存问题、动机与假设、验证方案。
- `claim`、`plan`、`alternatives`、`evidence`：适用于以主张和证据表达的输出。后两项可以使用字符串数组。

可以只用一种形式。示例为虚构内容：

```json
{"id":"example-001","system":"YourSystem","paper_id":"synthetic/example001","material":{"proposal":"This is a synthetic format example. State the new question, hypothesis, distinction from prior work and a falsifiable validation plan here."}}
```

真实评测时，将 `paper_id` 换成 `data/papers.jsonl` 中的值。评分器允许虚构 ID 以便离线演示，不会替你核验论文来源；正式汇总应检查 ID 是否在选定 split 中。同一个系统、同一篇论文只提交一个最终问题。程序会拒绝重复 ID、重复系统/论文组合、空正文、材料顶层的未知字段及非法 JSON。

评分模型只收到 `material`，不会收到 `system`、论文目录或提交 ID。模型不会自动读取 PDF、检索文献或判断“全球首次”。输入文本中出现的科学事实和假设仍需独立核验。

## 4. 先检查，再正式运行

离线检查示例，不联网、不产生模型费用：

```bash
python -m asdbench.score --input examples/submissions.jsonl --output runs/example_prepared.jsonl --prepare-only
```

正式评分：

```bash
python -m asdbench.score --input examples/submissions.jsonl --output runs/example_scores.jsonl --workers 2
```

更换为自己的问题文件：

```bash
python -m asdbench.score --input my_submissions.jsonl --output runs/my_scores.jsonl --workers 5
```

`--workers` 是同时评分的任务数，每个任务最多一个在途评分请求；失败最多重试 `--attempts` 次，默认 3 次。并发上限受模型服务限制，先用小规模试跑，不要直接启动全量。温度固定为 0，默认最多生成 2,048 token，要求关闭 thinking。

接口不支持 JSON Schema 时，使用以下命令，程序仍会严格校验返回内容：

```bash
python -m asdbench.score --input my_submissions.jsonl --output runs/compatible_scores.jsonl --response-format json_object --omit-thinking-flag
```

若服务连 JSON mode 都不支持，可将 `--response-format` 设为 `none`。这些选项会改变请求设置，记录在评分签名中。首次运行前选定，避免与已有输出混用。

更多参数：

```bash
python -m asdbench.score --help
```

## 5. 查看输出与断点续跑

输出是 JSONL，一行一个提交的评分记录。成功记录包含：

- `id`、`system`、`paper_id`：对应输入。
- `subscores`：16 个子项。
- `Novelty`、`Feasibility`、`Value`、`Risk`、`Total`：四维与总分。
- `review_model`、`prompt_sha256`、`material_sha256`、`signature`、`reviewed_at`：复核信息。
- `aggregate_corrected`：是否修正了模型自行计算的汇总分。

正文、密钥、服务地址以及原始服务错误响应不会写入评分输出。`runs/` 已在 `.gitignore` 中排除。

查看第一条结果（PowerShell）：

```powershell
Get-Content runs/example_scores.jsonl -TotalCount 1
```

如需 Excel 表格，可在本地导出 CSV；文件不会加入开源包：

```bash
python scripts/export_scores.py --input runs/example_scores.jsonl --output runs/example_scores.csv
```

CSV 包含身份、16 子项、四维、总分与状态，不包含问题正文。`null` 在 CSV 中保留空白，失败行不会填成 0。

每完成一条就原子写入文件。再次运行**完全相同的命令**会复用已完成项，只重试未完成项。更换模型、服务地址、prompt、输出预算、正文或响应模式时，改用新的输出文件。不要同时启动两个进程写同一个输出文件。

`status=prepared` 仅表示格式检查完成，没有评分。`status=failed` 表示重试后仍无法取得合法评审，不是 0 分，不能混入均值。退出码 0 表示本批完成，1 表示存在失败，2 表示参数或输入有误，130 表示用户中断。

## 6. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 找不到 `asdbench` | 在仓库根目录执行命令；不要直接运行 `asdbench/score.py` |
| API 根地址错误、404 | 确认地址前缀包含服务需要的 `/v1`，结尾不重复 `/chat/completions` |
| 401 / 403 | 检查密钥、账户权限及模型授权；不要把密钥贴进 issue |
| 服务拒绝 JSON Schema 或 thinking 字段 | 使用 `--response-format json_object --omit-thinking-flag` |
| 重试后失败 | 先检查服务连通性、请求限额和输出预算；必要时增大 `--max-tokens`，并使用新输出文件 |
| 输入超过预算 | 默认正文上限 250,000 UTF-8 字节，可调 `--max-input-bytes`；程序不会截断正文 |
| 模型上下文不够 | 字节上限不等于 token 上限，需按服务实际上下文检查；该工具不执行服务专有的 tokenizer 调用 |
| 已有输出与当前配置不一致 | 用新的输出文件，避免混用不同条件的评分 |

## 7. 不调用云端的输入检查

```bash
python -m asdbench.score --input examples/submissions.jsonl --output runs/input_check.jsonl --prepare-only
python scripts/download_pdfs.py --dry-run --limit 5
```

第一条检查问题输入和评分 prompt 的格式，第二条检查论文索引并预览下载任务。两条命令都不访问云端模型，不消耗模型额度，也不会下载 PDF。科学内容的有效性仍需要独立判断。
