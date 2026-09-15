# IndexTTS 分发条款核对表

状态：current

本文把 Voice Studio 中 IndexTTS 相关源码的固定许可和发布动作列成可审阅清单。它不是法律
意见，也不替维护者、发布者或下游使用者签署或接受 Bilibili Model Use License Agreement。
把许可文本放进源码包只是其中一项动作，不能单独完成下游合同、使用限制、第三方授权或
其他分发责任。

## 固定许可来源

| 范围 | 固定官方版本 | 本仓库保留的文本 |
|---|---|---|
| IndexTTS 1.5 映射部分 | [`v1.5.0` / `9098497272d5803bae46cbaf5154cf2ba48f6866`](https://github.com/index-tts/index-tts/releases/tag/v1.5.0) 的 [Apache-2.0 LICENSE](https://github.com/index-tts/index-tts/blob/9098497272d5803bae46cbaf5154cf2ba48f6866/LICENSE) | [`IndexTTS1-Apache-2.0.txt`](../../third_party_licenses/IndexTTS1-Apache-2.0.txt) |
| IndexTTS 2.0 映射部分 | [`v2.0.0` / `830f6f8f94a51fea23ab1d639027a86200075a4e`](https://github.com/index-tts/index-tts/commit/830f6f8f94a51fea23ab1d639027a86200075a4e) 的 [Bilibili Model Use License Agreement](https://github.com/index-tts/index-tts/blob/830f6f8f94a51fea23ab1d639027a86200075a4e/LICENSE) | [`IndexTTS2-bilibili-model-use-license.txt`](../../third_party_licenses/IndexTTS2-bilibili-model-use-license.txt) |

## IndexTTS 1.5 / Apache-2.0

若分发 v1 映射部分，发布包至少需要：

- 向接收者提供 Apache-2.0 完整文本；
- 在修改过的文件中提供显著的修改说明；
- 保留适用于所分发源码的版权、专利、商标和署名通知；
- 若所分发上游版本包含适用的 `NOTICE`，按 Apache-2.0 第 4(d) 条处理。固定的官方
  `v1.5.0` 根目录没有 `NOTICE` 文件，但发布者仍需检查最终打包内容中的其他组件。

本仓库新增的 `IndexTTS1-Apache-2.0.txt` 是固定官方 v1.5 许可的完整文本副本，不把
Apache-2.0 条款改写为 Voice Studio 自有许可。

## IndexTTS 2.0 / Bilibili 协议

Voice Studio 源码保留 v2 映射部分，并同时保留固定协议、来源映射和要求的分发声明。
个人保留或使用这部分代码不自动扩大公开分发权利；实际分发仍由分发者按完整协议判断并
履行适用责任。本文记录仓库内能落实的材料，不把文档提交冒充外部合同或法律确认。

### 第 3.4 条：下游接收者

如果发布者选择依协议分发，协议文本要求发布者：

- 确保任何 Model 或 Derivative Work 的下游接收者遵守协议，并向下游施加适当合同条款；
  下游违约后果也由发布者承担。公开仓库里附带许可文件本身不能建立或验证这些合同安排；
- 在每份使用的 Model 或 Derivative Work 副本中保留全部原始版权通知和协议副本；
- 不得用 bilibili indextts2 或其 Derivative Work 改进其他 AI 模型，协议列明的例外是
  bilibili indextts2 本身、其 Derivative Works 或非商业 AI 模型。

### 第 4.1 条：使用与分发限制

协议第 4.1(a) 要求分发 Derivative Work 时，在分发页或随附文档中清楚写明以下原文：

> Any modifications made to the original model in this Derivative Work are not endorsed, warranted,
> or guaranteed by the original right-holder of the original model, and the original right-holder
> disclaims all liability related to this Derivative Work.

Voice Studio 的分发页或随附文档必须保留这段原文；改写成一般免责声明不足以证明已经满足
该项原文声明。第 4.1 条还要求：

- 使用中含有第三方数据或权重时，使用者自行取得全部必要授权并承担合规责任；
- 不得把 Model 或 Derivative Work 用于违反生成地或使用地法律、监管要求的目的；
- 能生成内容时，必须确保生成内容符合适用法律和监管要求。

### 发布者必须另外审阅的事项

上面只展开第 3.4 和 4.1 条，不能替代完整协议。发布者还需审阅并判断至少包括：

- 第 2.2 条的月活和年收入门槛是否适用；达到任一门槛时，协议要求先取得单独书面许可；
- 第 4.2 条列举的高风险部署及相应合规责任；
- 第 4.3 条的第三方索赔处理和赔偿责任；
- 第 5 条的撤销、商标和权利终止安排；
- 第 6 条的适用法律与争议解决，以及第 9 条规定的中英文冲突处理。

## 仓库内已落实与外部条件

仓库内已经保留固定许可全文、来源映射、源码修改标注、第三方通知和第 4.1(a) 条要求的
不背书原文。仓库文件无法客观完成或证明的事项包括：与下游建立适当合同、核实每个分发者
的规模门槛和司法辖区、取得第三方数据或权重授权，以及在需要时取得权利人的单独书面许可。
这些条件由实际分发者结合自己的身份、用途、接收者和地区处理。
