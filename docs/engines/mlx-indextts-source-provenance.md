# MLX-IndexTTS 来源记录

状态：current

本文只记录 `mlx_indextts` 源码从哪里进入 Voice Studio，以及仍未解决的许可边界。它不是
法律意见，也不把运行兼容、模型权重许可或算法相似性自动等同为源码版权结论。

## 直接上游

Voice Studio 的开发仓库审计过 `solar2ain/mlx-indextts` 的原始 Git 提交。首次公开版可以是
不含开发历史的干净源码快照，因此公开使用者不需要、也不能依赖本仓库仍包含这些旧提交；
下面的固定上游链接才是公开可复查的来源证据：

| 范围 | 固定提交 | 进入本仓库的内容 |
|---|---|---|
| MLX IndexTTS 1.x 初始实现 | [`e7e6c47edf4a88e51921422aaf5fc21649689318`](https://github.com/solar2ain/mlx-indextts/commit/e7e6c47edf4a88e51921422aaf5fc21649689318) | `mlx_indextts` 基础包、模型结构、转换、推理和 CLI |
| MLX IndexTTS 2.x 初始实现 | [`fc8f92536656b3df79a55b7270f63f4cf14a158c`](https://github.com/solar2ain/mlx-indextts/commit/fc8f92536656b3df79a55b7270f63f4cf14a158c) | v2 转换与推理、S2Mel、MaskGCT/CAMPPlus 辅助源码 |
| Voice Studio 分叉前的最后共享实现 | [`1026564f418e633e885df349e42ccf31a0ea9884`](https://github.com/solar2ain/mlx-indextts/commit/1026564f418e633e885df349e42ccf31a0ea9884) | 速度、静音压缩和峰值归一化；作为比较基线 |

公开上游地址：<https://github.com/solar2ain/mlx-indextts>

该上游在固定提交中以 MIT 发布，并声明 `Copyright (c) 2026 Didi`。原文保存在
`third_party_licenses/MLX-IndexTTS-MIT.txt`。Voice Studio 后续修改不能移除或替代这份
上游通知。

## Voice Studio 后续修改

以 `1026564f418e633e885df349e42ccf31a0ea9884` 为比较基线，Voice Studio 当前修改了：

- 包入口、CLI 和版本解析；
- v1/v2 模型转换与运行时平台选择；
- v1/v2 推理、离线预处理权重检查和音频后处理；
- GPT 运行时兼容；
- MaskGCT 权重加载边界；
- 新增 `model_artifacts.py` 与 `version.py`。

干净首发快照不保留上述旧提交，因此本文直接保存审计结果，不要求下游运行引用旧 SHA 的
`git diff`。当前相对最后共享实现有内容变化的路径为：`mlx_indextts/__init__.py`、
`cli.py`、`convert.py`、`convert_v2.py`、`generate.py`、`generate_v2.py`、
`indextts/utils/maskgct_utils.py`、`models/gpt.py`、`models/gpt_v2.py`；另新增
`model_artifacts.py` 与 `version.py`。其余 `mlx_indextts` 路径沿用固定 MLX 上游基线。

## 官方固定参照版本

| 参照 | 固定提交 | 该提交中的许可 | 用途与边界 |
|---|---|---|---|
| [IndexTTS 1.5 `v1.5.0`](https://github.com/index-tts/index-tts/releases/tag/v1.5.0) | `9098497272d5803bae46cbaf5154cf2ba48f6866` | [Apache-2.0](https://github.com/index-tts/index-tts/blob/9098497272d5803bae46cbaf5154cf2ba48f6866/LICENSE)；[本仓库副本](../../third_party_licenses/IndexTTS1-Apache-2.0.txt) | 复查 MLX 1.x 的模块对应关系；不声称这是作者当时未记录的 checkout |
| IndexTTS 2.0 `v2.0.0` | [`830f6f8f94a51fea23ab1d639027a86200075a4e`](https://github.com/index-tts/index-tts/commit/830f6f8f94a51fea23ab1d639027a86200075a4e) | [Bilibili Model Use License Agreement](https://github.com/index-tts/index-tts/blob/830f6f8f94a51fea23ab1d639027a86200075a4e/LICENSE) | 复查 MLX 2.x 及内嵌源码；不声称这是作者当时未记录的 checkout |

## 与官方源码的文件映射

MLX 上游自己的 [1.5 对齐文档](https://github.com/solar2ain/mlx-indextts/blob/e7e6c47edf4a88e51921422aaf5fc21649689318/docs/indextts_1.5_alignment.md)
和 [2.0 对齐文档](https://github.com/solar2ain/mlx-indextts/blob/fc8f92536656b3df79a55b7270f63f4cf14a158c/docs/indextts_2.0_alignment.md)
明确记录了“将 IndexTTS 从 PyTorch 移植到 MLX”、逐层导出中间结果并匹配官方行为。
固定源码比较进一步得到以下可复查映射。

### IndexTTS 1.5 到 MLX 1.x

这些文件不是逐字节副本，但类名、职责、权重键和对齐记录均指向对应官方模块：

| MLX 路径 | 官方 `v1.5.0` 参照路径 |
|---|---|
| `models/ecapa_tdnn.py` | `indextts/BigVGAN/ECAPA_TDNN.py` |
| `models/bigvgan.py` | `indextts/BigVGAN/models.py`、`indextts/BigVGAN/bigvgan.py` |
| `models/activations.py` | `indextts/BigVGAN/alias_free_torch/resample.py` 及 alias-free activation 实现 |
| `models/conformer.py` | `indextts/gpt/conformer_encoder.py` 及 `indextts/gpt/conformer/` |
| `models/gpt.py` | `indextts/gpt/model.py` |
| `models/perceiver.py` | `indextts/gpt/perceiver.py` |
| `normalize.py`、`tokenizer.py` | `indextts/utils/front.py` |
| `generate.py` | `indextts/infer.py` |

### IndexTTS 2.0 到 MLX 2.x

下列非空文件在 MLX 上游提交 `fc8f925...` 与官方 `v2.0.0` 字节相同：

- `indextts/s2mel/modules/audio.py`；
- `indextts/s2mel/modules/campplus/layers.py`；
- `indextts/utils/maskgct/models/codec/amphion_codec/quantize/` 下的
  `factorized_vector_quantize.py`、`lookup_free_quantize.py`、`vector_quantize.py`；
- `indextts/utils/maskgct/models/codec/kmeans/vocos.py`。

另有三个文件除导入路径加上 `mlx_indextts` 包前缀外，与官方文件相同：

- `indextts/s2mel/modules/campplus/DTDNN.py`；
- `indextts/utils/maskgct/models/codec/amphion_codec/quantize/residual_vq.py`；
- `indextts/utils/maskgct/models/codec/kmeans/repcodec_model.py`。

这些目录中一部分源码自身来自 Amphion、3D-Speaker 等项目，仍按下面“内嵌第三方源码”
记录其原始条款；“经官方仓库逐字节复查”不表示 Bilibili 取得或替代了第三方权利。

MLX 改写文件还存在以下结构对应关系：

| MLX 路径 | 官方 `v2.0.0` 参照路径 |
|---|---|
| `generate_v2.py` | `indextts/infer_v2.py` |
| `models/gpt_v2.py` | `indextts/gpt/model_v2.py` |
| `models/bigvgan_v2.py` | `indextts/s2mel/modules/bigvgan/bigvgan.py` |
| `models/s2mel/cfm.py` | `indextts/s2mel/modules/flow_matching.py` |
| `models/s2mel/dit.py` | `indextts/s2mel/modules/gpt_fast/model.py` 及 DiT 编排 |
| `models/s2mel/length_regulator.py` | `indextts/s2mel/modules/length_regulator.py` |
| `models/s2mel/wavenet.py` | `indextts/s2mel/modules/wavenet.py` |

## 源码内的修改标注

上述 v1 MLX 改写文件顶部已标明官方 `v1.5.0` 固定参照、Apache-2.0 和“由
MLX-IndexTTS 从 PyTorch 移植到 MLX”的修改事实；Voice Studio 后续实际修改过的
`models/gpt.py`、`generate.py` 另明确标注了后续修改。v2 MLX 改写文件顶部同样标明官方
`v2.0.0` 固定参照和本通知入口。

内嵌 CAMPPlus 与 Amphion 文件保留原始版权和许可头；仅调整包导入路径的 `DTDNN.py`、
`residual_vq.py`、`repcodec_model.py` 已补充修改说明。与官方 v2 字节对应但原文件没有
版权头的 `audio.py` 已增加来源说明，未改运行逻辑。

## 内嵌第三方源码

MLX 上游的 MIT 许可证不取代其内嵌第三方源码自己的条款：

| 路径 | 已知来源 | 保留条款 |
|---|---|---|
| `mlx_indextts/indextts/utils/maskgct/` | Amphion / MaskGCT | `third_party_licenses/Amphion-MIT.txt` |
| `mlx_indextts/indextts/s2mel/modules/campplus/` | 3D-Speaker CAMPPlus | `third_party_licenses/3D-Speaker-Apache-2.0.txt` |
| `mlx_indextts/models/bigvgan.py`、`bigvgan_v2.py` 及相关转换 | NVIDIA BigVGAN-compatible code | `third_party_licenses/BigVGAN-MIT.txt` |

## 已关闭的来源问题与仍需决定的许可边界

`solar2ain/mlx-indextts` 的首个实现提交说明其结构和输出与一个本地 PyTorch IndexTTS
checkout 做过逐层对齐，但没有保存该官方 checkout 的仓库提交，也没有说明每个 MLX 文件
是独立重写、接口兼容还是改写自官方源码。官方 IndexTTS 2.0 固定参照的许可把官方发布
的最终代码纳入“Model”，并约束其 Derivative Work。

逐文件比较已经能反驳“MLX 包与官方源码没有可识别派生关系”的假设，也把 v1 与 v2 的
来源和许可版本分开。缺失历史 checkout 不再妨碍采取保守的来源标注：v1 映射部分按官方
`v1.5.0` 的 Apache-2.0 义务处理，v2 映射部分不能被 MLX 上游的 MIT 声明覆盖。

源码包现已为 v1 映射部分保留 Apache-2.0 文本、适用署名和显著修改说明；实际历史
checkout 未记录的证据限制继续保留，固定参照 tag 不冒充实际来源提交。v2 映射部分随
源码保留，其 Bilibili 条款不能被 MLX 上游 MIT 声明覆盖。因此完整源码和 wheel 仍不能
描述为无条件 MIT 包。

本记录不替任何人签署或接受协议，也不声称仓库文件已经建立下游合同。实际分发包含 v2
映射部分时，分发者仍须按完整协议处理适用的外部责任。
第 3.4、4.1 条的具体分发提醒、上游不背书原文和其余待审条款见
[`indextts-distribution-terms.md`](indextts-distribution-terms.md)。该清单不把保留许可文件
等同于完成全部下游义务。
