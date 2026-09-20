# 我的瞄准知识库（SDK 模板包）

这是一个**能直接通过 Aiming Cookie 导入校验的最小合法知识库包**。复制本目录，把占位内容换成你自己的知识，就得到一个可分发的包。

## 这个包里有什么

| 文件 | 作用 | 你要不要改 |
|---|---|---|
| `manifest.json` | 包身份：ID、名字、作者、版本、兼容性声明 | **必改**（至少改 `pack_id` 和 `author`） |
| `knowledge/registry.json` | 知识层：解释、机制、cue、剂量、复测、停练规则等长正文 | **必改**（替换 3 条示例条目为你的内容） |
| `mapping.json` | 映射层（可选）："什么数值算什么信号"的规则 | 可选：不想动规则就删掉本文件（并同步删掉 manifest 里 `ac_compat.mapping_schema`） |
| `README.md` | 本文件：给拿到包的人看的说明 | 建议改 |

## 最少要做的四步

1. **改 `pack_id`**（`manifest.json`）：把 `com.example.my-kb` 换成你自己的反向域名风格 ID（小写字母、数字、点、连字符），例如 `com.coachwang.static-kb`。**ID 一旦发布就不要再改**——它是用户的安装键。
2. **改版本联动**：`knowledge/registry.json` 里 `registry_version` 必须严格等于 `<pack_id>@<pack_version>`。改了 `pack_version` 就要同步改它，否则校验不通过。
3. **替换示例条目**：`knowledge/registry.json` 里有三条示例（纯解释 / 可实验 / 处方），照着它们的字段结构写你的条目。条目里的 `signals`（如 `sparc low`）和 `metric_refs`（如 `sparc`）**只能从官方词汇表里取**，词汇表见 `knowledge/mapping/vocabulary.v1.json`；自创同义词请放进 `signal_aliases`。
4. **本地校验**：在 Aiming Cookie 仓库根目录运行

   ```
   python -m kovaak_tracker.coach.knowledge_pack validate <你的包目录>
   ```

   输出 `OK ...` 即合法；报错会给出错误码和原因，对照 `../SPEC.md` 第 13 节速查修复。

## 三条示例条目分别演示什么

- `kb.my-kb.explanation.sparc` —— `explanation_only`：最浅档，只提供解释，不参与诊断，不能带 cue/剂量等训练字段。
- `kb.my-kb.experiment.sparc` —— `candidate_experiment`：可参与诊断和实验建议，因此必须带 `observation_refs`、`quality_prerequisites` 和 `cue`/`dose_guardrail`/`matched_retest`/`stop_adjust_rule`。
- `prescription.sparc.smoothness` —— `prescription.*` 处方条目（最高档）：额外带 `near_transfer_retest` 和 `scenario_prescription`。**注意**：`scenario_profile_ref` 只能引用官方已审核场景（本例引用 `scenario:static.1wall_6targets_small@1`），不能自造场景档案。

## 下一步阅读

- `../SPEC.md` —— 完整格式规范：字段表、6 条收窄规则、mapping 规则参考、激活与回退语义。
- `../docs/data-reference.md` —— 数据说明：每类数据从哪来、反映什么、分析管线怎么用。
- `knowledge/mapping/official.v1.json`（仓库内）—— 官方包是最好的参照物，23 条规则全部可以对照模仿。
