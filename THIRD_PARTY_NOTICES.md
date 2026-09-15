# Third-party notices

Voice Studio includes original project code, compatibility implementations, and a small number of
vendored upstream source files. The root `LICENSE` applies only where the Voice Studio contributors
have the right to grant those terms. It does not replace the licenses of upstream source, model
weights, datasets, external runtimes, or hosted services.

## MLX-IndexTTS source lineage

Most of `mlx_indextts` came from [`solar2ain/mlx-indextts`](https://github.com/solar2ain/mlx-indextts).
The Voice Studio development-history audit matched the upstream objects beginning at
[`e7e6c47edf4a88e51921422aaf5fc21649689318`](https://github.com/solar2ain/mlx-indextts/commit/e7e6c47edf4a88e51921422aaf5fc21649689318);
the last shared implementation before Voice Studio-specific changes is
[`1026564f418e633e885df349e42ccf31a0ea9884`](https://github.com/solar2ain/mlx-indextts/commit/1026564f418e633e885df349e42ccf31a0ea9884).
Clean source releases need not contain those historical objects; the fixed public links are the
reproducible source record.
The upstream MIT notice, including `Copyright (c) 2026 Didi`, is retained at
`third_party_licenses/MLX-IndexTTS-MIT.txt`. That notice covers rights granted by the MLX-port
upstream; it does not relicense source that came from another project. The stable path and commit
map is recorded in `docs/engines/mlx-indextts-source-provenance.md`.

The official source must be split by version. Official IndexTTS 1.5
[tag `v1.5.0`](https://github.com/index-tts/index-tts/releases/tag/v1.5.0), fixed ref
`9098497272d5803bae46cbaf5154cf2ba48f6866`,
contains an [Apache License 2.0](https://github.com/index-tts/index-tts/blob/9098497272d5803bae46cbaf5154cf2ba48f6866/LICENSE).
The complete fixed text is retained at
[`third_party_licenses/IndexTTS1-Apache-2.0.txt`](third_party_licenses/IndexTTS1-Apache-2.0.txt).
Official IndexTTS 2.0 tag `v2.0.0`, commit
[`830f6f8f94a51fea23ab1d639027a86200075a4e`](https://github.com/index-tts/index-tts/commit/830f6f8f94a51fea23ab1d639027a86200075a4e),
contains the [Bilibili Model Use License Agreement](https://github.com/index-tts/index-tts/blob/830f6f8f94a51fea23ab1d639027a86200075a4e/LICENSE).
That agreement defines its “Model” to include published final code and separately defines
Derivative Works.

## Derivation review result and distribution scope

The source comparison is no longer an unmapped, repository-wide unknown. The fixed comparison
record identifies:

- the MLX 1.x files that correspond structurally to official IndexTTS 1.5 modules;
- the MLX 2.x files that correspond structurally to official IndexTTS 2.0 modules; and
- nine non-empty files that match official IndexTTS 2.0 byte-for-byte or differ only by the
  `mlx_indextts` package prefix.

The exact unrecorded checkout used by the MLX-port author is still unknown. The comparison therefore
uses the two fixed official release tags as reproducible reference baselines and does not claim that
either tag was the author's historical checkout. See the provenance record for the file map and
method.

The IndexTTS 1.5-derived portion must retain Apache-2.0 terms and applicable notices, including
prominent change notices for modified files. Do not describe the complete repository or
`mlx_indextts` wheel as unconditionally MIT-licensed. The IndexTTS 2.0 agreement is preserved at
[the retained IndexTTS 2.0 agreement](third_party_licenses/IndexTTS2-bilibili-model-use-license.txt).
Voice Studio modifications are not
endorsed, warranted, or guaranteed by the original IndexTTS right-holder, which disclaims liability
for those modifications.

If the release owner chooses to distribute the IndexTTS 2.0-derived portion under that agreement,
Sections 3.4 and 4.1 require more than carrying the license file. They include downstream compliance
and contractual responsibilities, preservation of notices and the agreement in copies, restrictions
on improving other AI models, third-party authorization and lawful-use duties, and a required
non-endorsement statement on the distribution page or in accompanying documentation. The exact
statement and a review checklist are preserved in the
[IndexTTS distribution-terms checklist](docs/engines/indextts-distribution-terms.md). This repository does not record that the release owner
has completed contractual arrangements with downstream recipients or other responsibilities that
cannot be performed by changing repository files. Distribution that includes the IndexTTS 2.0-derived
portion remains subject to those terms; if a distributor cannot meet them, the distributor must
change the distribution scope or obtain an applicable clarification or permission.

The agreement's required statement is:

> Any modifications made to the original model in this Derivative Work are not endorsed, warranted,
> or guaranteed by the original right-holder of the original model, and the original right-holder
> disclaims all liability related to this Derivative Work.

## Source included in this repository

| Component | Included paths | Upstream | Terms retained here |
|---|---|---|---|
| MLX-IndexTTS port | most of `mlx_indextts/` | https://github.com/solar2ain/mlx-indextts at `1026564f418e633e885df349e42ccf31a0ea9884` | MIT for rights granted by that upstream; see `third_party_licenses/MLX-IndexTTS-MIT.txt`; underlying sources keep their own terms |
| Official IndexTTS 1.5-adapted MLX implementation | mapped v1 paths in `docs/engines/mlx-indextts-source-provenance.md` | https://github.com/index-tts/index-tts at `9098497272d5803bae46cbaf5154cf2ba48f6866` | Apache-2.0; see [`IndexTTS1-Apache-2.0.txt`](third_party_licenses/IndexTTS1-Apache-2.0.txt); retain applicable notices |
| Official IndexTTS 2.0-adapted MLX implementation and matched source | mapped v2 paths in `docs/engines/mlx-indextts-source-provenance.md` | https://github.com/index-tts/index-tts at `830f6f8f94a51fea23ab1d639027a86200075a4e` | Bilibili Model Use License Agreement; see `third_party_licenses/IndexTTS2-bilibili-model-use-license.txt`; applicable downstream duties are summarized below |
| Amphion / MaskGCT codec utilities | `mlx_indextts/indextts/utils/maskgct/` | https://github.com/open-mmlab/Amphion | MIT; see `third_party_licenses/Amphion-MIT.txt` |
| 3D-Speaker CAMPPlus | `mlx_indextts/indextts/s2mel/modules/campplus/` | https://github.com/modelscope/3D-Speaker | Apache-2.0; see `third_party_licenses/3D-Speaker-Apache-2.0.txt` |
| NVIDIA BigVGAN-compatible vocoder code | `mlx_indextts/models/bigvgan.py`, `mlx_indextts/models/bigvgan_v2.py` and related conversion code | https://github.com/NVIDIA/BigVGAN | MIT; see `third_party_licenses/BigVGAN-MIT.txt`; exact derivation still belongs in the provenance review |

Source files may contain additional attribution comments. Those comments remain part of the notice
and must not be removed.

## Models and external runtimes are not distributed

The Git repository, source archive, and Python wheel do not contain model weights. A source link or
download button is not a grant of rights. Users must review the terms at the pinned model source
before downloading or converting a model. In particular:

- official IndexTTS weights and derived/converted weights remain subject to the official IndexTTS
  terms;
- MaskGCT preprocessing weights are marked CC-BY-NC-4.0 and are not a commercial-use dependency;
- OmniVoice pretrained weights are marked non-commercial by their publisher;
- a model with missing or unverified weight terms is not eligible for automatic download.

External engines such as CosyVoice, F5-TTS, EmotiVoice, Qwen, Confucius4, OmniVoice and their model
weights retain their own code and model terms. They are installed separately and are not relicensed
by Voice Studio.

This notice is a provenance record, not legal advice. Release owners remain responsible for checking
the exact versions they distribute.
