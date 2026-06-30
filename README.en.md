# v_dump — Vertica logical backup tool (client-side)

[🇰🇷 한국어](README.md) · **🇬🇧 English** · [🇯🇵 日本語](README.ja.md) · [🇨🇳 中文](README.zh.md)

> In environments where you can't touch the server, a tool that logically backs up and restores
> Vertica's data, structure, and procedures **using nothing but a network connection (5433)**.
> **A `pg_dump` for Vertica, built for constrained environments.**

Data is dumped to `COPY`-compatible `.dat` files (pipe-delimited), and structure is dumped as DDL. Restore is
a single line: `vsql -f load.sql`. No server filesystem, SSH, or admin privileges required at all.

---

## Why it was built — where vbr can't go

Vertica's official backup tool **vbr** presupposes node **filesystem + SSH access**, plus a backup
repository the nodes can write to. But when the target Vertica is **running on Docker without volumes, with only
client access (5433) available**, vbr **simply cannot run at all**.

v_dump is the tool for exactly that gap — it backs up **with nothing but a single read-only SQL connection**.

> By analogy, it's like `pg_dump`. It's slower than a filesystem snapshot, but because it's **logical, portable, and selective**,
> the whole world uses it every day. v_dump fills that same role for constrained Vertica environments.

---

## v_dump vs vbr

| | **vbr (official)** | **v_dump** |
|---|---|---|
| Method | Physical — copies ROS files as-is | Logical — `SELECT` → text `.dat` + DDL |
| Access requirements | Node **filesystem + SSH** | Only a **network (5433) read-only account** |
| Server footprint | Installed and run on the server | **0** (client-side only) |
| Portability | Bound to the same/compatible cluster | To **any Vertica** (regardless of version or node count) |
| Selectivity | Possible per-object (heavy) | A single table up to a whole schema, **freely** |
| Output | Opaque physical files | **Human-readable** text |
| Consistency | Epoch snapshot (consistent) | Per-table SELECT (no point-in-time consistency) |
| Incremental | Supported | None (full extraction every time) |
| Scale/speed | Overwhelming at large scale (parallel node copy) | Pulled through a single client → slow |

**They are not competitors.** vbr is for *"full-DB disaster recovery in environments where you own the server"*, while v_dump is for
*"logical, selective, portable extraction over the network alone, in environments where you can't touch the server."*

### When to use which

- **Use v_dump** — when server/node access is impossible (volume-less Docker, locked-down managed services), when you need a
  logical, portable dump, when you only want specific tables/schemas, for prod→dev data migration, or for client-only deployment.
- **Use vbr** — when you have server access and the goal is very large full-DB backups, incrementals, or point-in-time-consistent DR.

---

## Advantages

| Advantage | Details |
|---|---|
| **No privileges** | A single read-only SQL connection. No SSH, node access, or server privileges needed → security-friendly |
| **Multi-node** | List nodes comma-separated in `host` → automatic failover + parallelism distributed across nodes |
| **Portability** | `.dat`+DDL loads into any Vertica with a different version or topology |
| **Selectivity** | Dump one table, several, or a whole schema, and restore just one |
| **Transparency** | Open the `.dat` and inspect/verify the contents as-is |
| **Air-gapped deployment** | One image + one bundle, zero installation on the target server (`docker`/`podman`) |
| **Speed** | Adaptive parallel dump/restore (automatic), row sharding for huge tables |
| **Transfer efficiency** | `--compress` (gzip) — drastically cuts transfer/storage size, COPY reads it directly, lossless |
| **Operational convenience** | Progress bar, idempotent DDL, automatic procedure inclusion, lossless escaping |

---

## How it works

```
Backup:   vertica-python ──SELECT──▶ value escape ──▶ <schema>.<table>.dat
          EXPORT_OBJECTS ──▶ schema.ddl.sql (+ matching procedures, idempotent)
Restore:  vsql ──COPY ... FROM LOCAL──▶ Vertica
```

- **`.dat`** — Vertica's default `COPY` convention (pipe-delimited, `\N` for NULL, backslash escaping) verbatim →
  the fastest reload. Newlines, tabs, and special characters round-trip **losslessly**.
- **Adaptive parallelism** — decided automatically based on the workload: sequential for tiny ones, table-parallel when
  there are many tables, row-level sharding for huge tables. Parallel on **both dump and restore**. Workers `min(cores, 4)` (tunable via `V_DUMP_JOBS`).
- **Compression** (`--compress`) — gzips the `.dat`. Lightens the air-gap transfer load, and restore reads the compressed file
  directly with `COPY ... GZIP`, **losslessly**.
- **Idempotent DDL** — uses `CREATE ... IF NOT EXISTS` / `OR REPLACE`, so it's safe to re-run.

---

## Quick start

```bash
# [build machine, internet ON] package everything in one shot
./docker/package.sh                  # → v_dump-deploy.tar.gz (image + wrapper + yaml + loader)

# [target host, air-gapped] unpack, load, and use right away
tar xzf v_dump-deploy.tar.gz && cd v_dump-deploy
./LOAD-ME.sh
./v_dump-docker.sh dump --schema YOUR_SCHEMA               # backup
./v_dump-docker.sh restore YOUR_SCHEMA/all --with-ddl      # restore
```

> For the **full flow** — installation, build, connection setup, backup/restore, operational scenarios, and troubleshooting — see **[`GUIDE.md`](GUIDE.md)**.
> This README is "what it is and why you'd use it"; the GUIDE is "how to use it."

---

## Limitations (honestly)

- **Point-in-time consistency** — because tables are read separately, if data changes mid-dump, inconsistencies between
  tables are possible (unlike vbr's epoch snapshot).
- **Scale** — because it pulls every row through a single client, it is fundamentally slow for very large full DBs.
  That territory belongs to vbr / `EXPORT TO`.
- **No incrementals** — every backup is a full extraction.

> Once server/container file access is secured, it can be extended to a high-speed path based on `EXPORT TO DELIMITED`
> (bypassing the ceiling of logical extraction). For now, it's the optimal configuration under the "5433 client access only" premise.

---

## Docs · Languages

- **[`GUIDE.md`](GUIDE.md)** — Docker/air-gapped operations guide (start to finish)
- Languages: [한국어](README.md) · [English](README.en.md) · [日本語](README.ja.md) · [中文](README.zh.md)

---

© 2026 염기승 (Gibseung Yeom) <duarltmd1@naver.com> — author & copyright holder of **v_dump**. All rights reserved.
