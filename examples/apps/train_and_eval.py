"""A training workflow: tasks, volumes, and functions working together.

@app.task gives each call its own dedicated pod that terminates when
the function returns — right for work that runs minutes to hours.
A Volume persists files between calls and across resources: train()
writes a checkpoint, evaluate() reads it from a different pod. The
volume is created on first use, and both tasks are automatically
placed in its datacenter.

    rp dev examples/apps/train_and_eval.py
"""

import runpod

app = runpod.App("trainer")

checkpoints = runpod.Volume("checkpoints", size=10)


@app.task(gpu="4090", volume=checkpoints)
def train(steps: int = 200):
    import torch
    import torch.nn as nn

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"training on {device}")

    # learn y = 3x + 1 from noisy samples
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

    # checkpoints.path is where the volume is mounted on this pod
    run_dir = checkpoints.path / "linear"
    run_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), run_dir / "model.pt")
    print(f"saved checkpoint to {run_dir}")
    return {"checkpoint": "linear", "final_loss": round(loss.item(), 5)}


@app.task(gpu="4090", volume=checkpoints)
def evaluate(checkpoint: str):
    import torch
    import torch.nn as nn

    model = nn.Linear(1, 1)
    model.load_state_dict(
        torch.load(checkpoints.path / checkpoint / "model.pt")
    )
    weight, bias = model.weight.item(), model.bias.item()
    print(f"learned: y = {weight:.3f}x + {bias:.3f}")
    return {"weight": round(weight, 2), "bias": round(bias, 2)}


@runpod.local_entrypoint
def main():
    out = train.remote()
    print("train finished:", out)

    fit = evaluate.remote(out["checkpoint"])
    print("evaluation:", fit)
