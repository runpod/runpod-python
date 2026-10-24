"""`rp catalog`: gpu and cpu pricing and stock search."""

from typing import Optional

import click

from runpod.rp_cli import compute
from runpod.rp_cli.api import api_errors
from runpod.rp_cli.output import json_option, print_json, print_table


def _money(value: Optional[float]) -> str:
    return f"${value:.2f}" if value else "-"


def _stock(entries: list[dict]) -> str:
    ranked = sorted(
        entries, key=lambda dc: -compute.availability_rank(dc["availability"])
    )
    return ", ".join(f"{dc['id']} {dc['availability'].lower()}" for dc in ranked)


def _filter_stock(records: list[dict], regions: tuple[str, ...], available: bool):
    allowed = (
        set(compute.resolve_regions(regions, compute.list_datacenters()))
        if regions
        else None
    )
    for record in records:
        row = dict(record)
        if allowed is not None:
            row["dataCenters"] = [
                dc for dc in record.get("dataCenters", []) if dc["id"] in allowed
            ]
            row["availability"] = compute.best_availability(
                dc["availability"] for dc in row["dataCenters"]
            )
        if not available or compute.availability_rank(row.get("availability")) > 0:
            yield row


def _unit_price(gpu: dict, product: str, cloud: Optional[str]) -> Optional[float]:
    price = gpu["price"]
    if product == "SERVERLESS":
        return price.get("serverless") or None
    if cloud:
        return price.get(cloud) or None
    offered = [
        value for value in (price.get("secure"), price.get("community")) if value
    ]
    return min(offered, default=None)


def _gpu_rows(
    query, product, count, regions, cloud, cuda, min_vram, max_price, available
) -> list[dict]:
    min_cuda, exact_cuda = compute.parse_cuda(cuda)
    gpus = compute.list_gpus(
        product=product,
        count=count,
        cloud=cloud.upper() if cloud else None,
        cuda_versions=exact_cuda,
        min_cuda_version=min_cuda,
    )
    if query:
        gpus = compute.resolve_gpus(query, gpus)
    if product == "SERVERLESS":
        gpus = [gpu for gpu in gpus if gpu.get("pool")]
    rows = []
    for row in _filter_stock(gpus, regions, available):
        unit = _unit_price(row, product, cloud)
        if min_vram and row["memory"] < min_vram:
            continue
        if max_price is not None and (unit is None or unit > max_price):
            continue
        row["count"] = count
        row["totalPrice"] = {
            tier: round(value * count, 4) if value else None
            for tier, value in row["price"].items()
        }
        rows.append(row)
    rows.sort(
        key=lambda row: (
            -compute.availability_rank(row.get("availability")),
            _unit_price(row, product, cloud) or float("inf"),
            row["name"],
        )
    )
    return rows


def _gpu_columns(product: str, count: int, cloud: Optional[str]):
    suffix = f" x{count}" if count > 1 else ""
    columns = [
        ("GPU", lambda row: row["name"]),
        ("VRAM", lambda row: f"{row['memory']}GB"),
    ]
    if product == "SERVERLESS":
        columns += [
            ("POOL", lambda row: row["pool"]),
            (f"$/HR{suffix}", lambda row: _money(row["totalPrice"].get("serverless"))),
        ]
    else:
        tiers = [cloud] if cloud else ["secure", "community"]
        for tier in tiers:
            columns.append(
                (
                    f"{tier.upper()} $/HR{suffix}",
                    lambda row, tier=tier: _money(row["totalPrice"].get(tier)),
                )
            )
        columns.append(
            (
                "MAX",
                lambda row: (
                    row["maxCount"].get(cloud)
                    if cloud
                    else max(row["maxCount"].values(), default=None)
                ),
            )
        )
    columns += [
        ("STOCK", lambda row: (row.get("availability") or "NONE").lower()),
        ("DATA CENTERS", lambda row: _stock(row.get("dataCenters") or [])),
    ]
    return columns


def _cpu_rows(query, product, vcpu, regions, available) -> list[dict]:
    cpus = compute.list_cpus(product=product, vcpu_count=vcpu)
    if query:
        cpus = compute.match_cpus(query, cpus)
        if not cpus:
            raise compute.ResolutionError(f"unknown cpu '{query}'")
    return sorted(
        _filter_stock(cpus, regions, available),
        key=lambda row: -compute.availability_rank(row.get("availability")),
    )


def _cpu_columns(product: str):
    tier = "serverlessPerVcpu" if product == "SERVERLESS" else "securePerVcpu"
    return [
        ("CPU", lambda row: row["id"]),
        ("NAME", lambda row: row["name"]),
        ("GROUP", lambda row: row["group"]),
        ("VCPU", lambda row: "{min}-{max}".format(**row["vcpu"])),
        ("RAM/VCPU", lambda row: f"{row['ramGbPerVcpu']:g}GB"),
        ("$/VCPU-HR", lambda row: _money(row["price"].get(tier))),
        ("STOCK", lambda row: (row.get("availability") or "NONE").lower()),
        ("DATA CENTERS", lambda row: _stock(row.get("dataCenters") or [])),
    ]


_DATACENTER_COLUMNS = [
    ("DATA CENTER", lambda row: row["id"]),
    ("REGION", lambda row: row["region"].lower().replace("_", " ")),
    (
        "VOLUME TYPES",
        lambda row: [kind.lower() for kind in row["networkVolumeTypes"]],
    ),
    ("GLOBAL NETWORK", lambda row: row["globalNetwork"]),
    ("COMPLIANCE", lambda row: row["compliance"]),
]


@click.command()
@click.argument("query", required=False)
@click.option(
    "--pod",
    "product",
    flag_value="POD",
    default=True,
    help="Pod pricing and stock (default).",
)
@click.option(
    "--serverless",
    "product",
    flag_value="SERVERLESS",
    help="Serverless pricing, pools, and stock.",
)
@click.option(
    "--cpu", "cpu_mode", is_flag=True, help="Search CPU flavors instead of GPUs."
)
@click.option(
    "--datacenters", is_flag=True, help="List data centers and their regions."
)
@click.option(
    "--count",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="GPUs per pod or worker; prices and stock account for it.",
)
@click.option(
    "--vcpu",
    type=click.IntRange(min=2),
    default=None,
    help="vCPUs per pod or worker (with --cpu).",
)
@click.option(
    "--region",
    "regions",
    multiple=True,
    help="Region (eu, na, asia), id prefix (us, eu-ro), or data center id. Repeatable.",
)
@click.option(
    "--cloud",
    type=click.Choice(["secure", "community"], case_sensitive=False),
    default=None,
    help="Pod cloud tier.",
)
@click.option(
    "--cuda",
    multiple=True,
    help="CUDA filter: '>=12.4' for a floor, 12.4 or 12.4,12.8 for exact versions.",
)
@click.option("--min-vram", type=int, default=None, help="Minimum VRAM per GPU in GB.")
@click.option("--max-price", type=float, default=None, help="Maximum USD per GPU-hour.")
@click.option("--available", is_flag=True, help="Only show hardware that is in stock.")
@json_option
def catalog(
    query,
    product,
    cpu_mode,
    datacenters,
    count,
    vcpu,
    regions,
    cloud,
    cuda,
    min_vram,
    max_price,
    available,
    as_json,
):  # pylint: disable=too-many-arguments
    """Search GPU and CPU pricing and stock by data center.

    QUERY is a human-readable GPU (4090, h100, a100-sxm4), a serverless
    pool id, or with --cpu a CPU flavor.

    \b
      rp catalog h100 --count 2 --region eu --cuda '>=12.4'
      rp catalog 4090 --serverless
      rp catalog --cpu --vcpu 8 --available
      rp catalog --datacenters --region us
    """
    if cpu_mode and datacenters:
        raise click.UsageError("use either --cpu or --datacenters")
    if cloud and product == "SERVERLESS":
        raise click.UsageError("--cloud applies to pods only")
    cloud = cloud.lower() if cloud else None
    if cpu_mode and cloud == "community":
        raise click.UsageError("--cpu is only available in the secure cloud")

    with api_errors():
        if datacenters:
            values = regions + ((query,) if query else ())
            rows = compute.list_datacenters()
            if values:
                allowed = set(compute.resolve_regions(values, rows))
                rows = [row for row in rows if row["id"] in allowed]
            columns, empty = _DATACENTER_COLUMNS, "no data centers match"
        elif cpu_mode:
            rows = _cpu_rows(query, product, vcpu, regions, available)
            columns, empty = _cpu_columns(product), "no cpu flavors match"
        else:
            rows = _gpu_rows(
                query,
                product,
                count,
                regions,
                cloud,
                cuda,
                min_vram,
                max_price,
                available,
            )
            columns, empty = _gpu_columns(product, count, cloud), "no gpus match"

    if as_json:
        print_json(rows)
        return
    print_table(rows, columns, empty)
