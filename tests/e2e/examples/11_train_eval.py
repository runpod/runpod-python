"""a real workflow: train on one gpu pod, save to a volume, eval on another.

    rp dev tests/e2e/examples/11_train_eval.py --once
"""

import runpod
from runpod import App, Volume

app = App("ex-train-eval")

models = Volume("ex-models", size=10)


@app.task(gpu="4090", volume=models)
def train(steps: int = 200):
    import json

    import torch
    import torch.nn as nn

    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(0)

    # tiny regression problem: y = 3x + 1 with noise
    x = torch.randn(2048, 1, device=device)
    y = 3 * x + 1 + torch.randn_like(x) * 0.05

    model = nn.Linear(1, 1).to(device)
    opt = torch.optim.SGD(model.parameters(), lr=0.05)
    for step in range(1, steps + 1):
        loss = ((model(x) - y) ** 2).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
        if step % 50 == 0:
            print(f"step {step}/{steps} loss={loss.item():.5f}")

    run_dir = models.path / "linear-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), run_dir / "model.pt")
    (run_dir / "meta.json").write_text(json.dumps({"steps": steps}))
    print(f"saved to {run_dir}")
    return {"checkpoint": "linear-run", "loss": round(loss.item(), 5)}


@app.task(gpu="4090", volume=models)
def evaluate(checkpoint: str):
    import torch
    import torch.nn as nn

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = nn.Linear(1, 1).to(device)
    model.load_state_dict(
        torch.load(models.path / checkpoint / "model.pt", map_location=device)
    )
    weight = model.weight.item()
    bias = model.bias.item()
    print(f"learned y = {weight:.3f}x + {bias:.3f}")
    return {"weight": round(weight, 2), "bias": round(bias, 2)}


@runpod.local_entrypoint
def main():
    out = train.remote()
    print("train:", out)
    fit = evaluate.remote(out["checkpoint"])
    print("eval:", fit)
    assert abs(fit["weight"] - 3.0) < 0.3
    assert abs(fit["bias"] - 1.0) < 0.3


if __name__ == "__main__":
    main()
