"""Fuse an mlx-vlm LoRA adapter into its 4bit base, as a model folder MLX Serve can load.

MLX Serve does not load adapters, so the fine-tuned judge model is the base with the adapter
merged in and the language model re-quantized at --bits (default 8). Re-quantizing at the base's
4 bits rounds most of the LoRA delta away: the r1 judge kept F1 0.80 as an adapter but fell to
0.46 fused at 4 bits (validation rows, 2026-10-17).

    <mlx-vlm-venv>/bin/python scripts/finetune/fuse_lora.py \
        --model ~/.mlx-serve/models/empero-ai/Qwen3.8-9B-Distill-MLX-4bit \
        --adapter .local/finetune/judge/lora/r1 \
        --out ~/.mlx-serve/models/local/Qwen3.8-9B-pagegen-judge-r1
"""

import argparse
import json
import shutil
from pathlib import Path


def main() -> int:
    from mlx.utils import tree_unflatten
    from mlx_vlm.quant_utils import dequantize_model, quantize_model
    from mlx_vlm.utils import get_class_predicate, load, save_weights

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bits", type=int, default=8)
    args = parser.parse_args()
    base, out = args.model.expanduser(), args.out.expanduser()

    model, _ = load(str(base), adapter_path=str(args.adapter.expanduser()))
    fused = [
        (name, module.fuse(dequantize=True))
        for name, module in model.named_modules()
        if hasattr(module, "fuse") and hasattr(module, "lora_a")
    ]
    if not fused:
        raise SystemExit("no LoRA layers found; is --adapter an mlx-vlm adapter folder?")
    model.update_modules(tree_unflatten(fused))
    config = json.loads((base / "config.json").read_text())
    config.pop("quantization", None)
    config.pop("quantization_config", None)
    # the vision tower stays unquantized, as in the base
    model, config = quantize_model(dequantize_model(model), config, 64, args.bits,
                                   quant_predicate=get_class_predicate(skip_vision=True))

    out.mkdir(parents=True, exist_ok=True)
    save_weights(out, model, donate_weights=True)
    # Tokenizer, chat template and processor files come from the base unchanged.
    for item in base.iterdir():
        if item.suffix == ".safetensors" or item.name == "model.safetensors.index.json":
            continue
        if item.is_dir():
            shutil.copytree(item, out / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, out / item.name)
    (out / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False))
    print(f"fused {len(fused)} LoRA layers into {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
