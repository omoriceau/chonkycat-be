#!/usr/bin/env python3
"""
Looks up the real Cognito pool/client IDs for one environment in AWS and
writes them into samconfig.toml, in place, without disturbing comments or
formatting anywhere else in the file.

Invoked by update-samconfig.sh — see that script for the user-facing
--help / usage text.
"""

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

TOP_LEVEL_STR_KEYS = {"stack_name", "region", "s3_bucket", "capabilities"}
TOP_LEVEL_BOOL_KEYS = {"confirm_changeset", "resolve_s3"}


def resolve_config_path(raw_path: str) -> Path:
    """
    Resolve and sanity-check the samconfig.toml path before any file
    access. This script takes its path from argv, so a caller running it
    directly with a bad/adversarial argument shouldn't be able to make it
    read or write an arbitrary file on the system.
    """
    path = Path(raw_path).resolve()
    if path.name != "samconfig.toml":
        sys.exit(f"Refusing to operate on '{path}' — expected a file named samconfig.toml.")
    if not path.is_file():
        sys.exit(f"{path} does not exist or is not a file.")
    return path


def warn(msg):
    print(f"[WARN]  {msg}", file=sys.stderr)


def aws_json(*args):
    try:
        out = subprocess.run(["aws", *args, "--output", "json"],
                              capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("aws CLI not found on PATH.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"aws {' '.join(args)} failed: {e.stderr.strip()}")
    return json.loads(out.stdout)


def esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def discover_cognito_values(environment: str, region: str) -> dict:
    """
    Look up the real Cognito pool/client IDs for this environment.
    Naming convention confirmed against dev: chonky-admins-<env> /
    chonky-customers-<env>. Skips (with a warning) any pool that doesn't
    exist yet rather than failing the whole run.
    """
    discovered = {}

    pools = aws_json("cognito-idp", "list-user-pools", "--max-results", "60", "--region", region)["UserPools"]
    pools_by_name = {p["Name"]: p["Id"] for p in pools}

    admin_pool_name = f"chonky-admins-{environment}"
    customer_pool_name = f"chonky-customers-{environment}"

    if admin_pool_name in pools_by_name:
        discovered["CognitoUserPoolId"] = pools_by_name[admin_pool_name]
        print(f"  found {admin_pool_name}: {discovered['CognitoUserPoolId']}")
    else:
        warn(f"No Cognito user pool named '{admin_pool_name}' found in {region} — leaving CognitoUserPoolId as-is.")

    if customer_pool_name in pools_by_name:
        customer_pool_id = pools_by_name[customer_pool_name]
        discovered["CustomerCognitoUserPoolId"] = customer_pool_id
        print(f"  found {customer_pool_name}: {customer_pool_id}")

        clients = aws_json("cognito-idp", "list-user-pool-clients",
                            "--user-pool-id", customer_pool_id, "--region", region)["UserPoolClients"]
        if not clients:
            warn(f"{customer_pool_name} has no app client — leaving CustomerCognitoAppClientId as-is.")
        else:
            preferred_name = f"{customer_pool_name}-client"
            match = next((c for c in clients if c.get("ClientName") == preferred_name), clients[0])
            if len(clients) > 1 and match["ClientName"] != preferred_name:
                warn(f"{customer_pool_name} has {len(clients)} app clients and none named "
                     f"'{preferred_name}' — using '{match['ClientName']}'. Pass "
                     f"CustomerCognitoAppClientId=... explicitly if this is wrong.")
            discovered["CustomerCognitoAppClientId"] = match["ClientId"]
            print(f"  found {match['ClientName']}: {match['ClientId']}")
    else:
        warn(f"No Cognito user pool named '{customer_pool_name}' found in {region} — "
             f"leaving CustomerCognitoUserPoolId/CustomerCognitoAppClientId as-is.")

    return discovered


def find_section_bounds(lines: list[str], environment: str) -> tuple[int, int]:
    """Locate the [<environment>.deploy.parameters] section's line range."""
    header_re = re.compile(rf'^\[{re.escape(environment)}\.deploy\.parameters\]\s*$')
    section_start = next((i for i, l in enumerate(lines) if header_re.match(l.strip())), None)
    if section_start is None:
        sys.exit(f"No [{environment}.deploy.parameters] section found in samconfig.toml.")

    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        if re.match(r'^\[.*\]\s*$', lines[i].strip()):
            section_end = i
            break
    return section_start, section_end


def find_parameter_overrides_bounds(lines: list[str], section_start: int, section_end: int):
    """Locate the parameter_overrides = [ ... ] list's line range within a section, if present."""
    for i in range(section_start, section_end):
        if lines[i].strip().startswith("parameter_overrides"):
            for j in range(i, section_end):
                if lines[j].rstrip().endswith("]"):
                    return i, j
            return i, None
    return None, None


def update_top_level_keys(lines: list[str], section_start: int, section_end: int, remaining: dict) -> list:
    """Update stack_name/region/s3_bucket/etc. lines in place. Pops handled keys out of `remaining`."""
    changes = []
    for i in range(section_start, section_end):
        m = re.match(r'^([A-Za-z_]\w*)\s*=(.*)$', lines[i].strip())
        if not m:
            continue
        key, old_raw = m.groups()
        if key not in remaining or (key not in TOP_LEVEL_STR_KEYS and key not in TOP_LEVEL_BOOL_KEYS):
            continue
        value = remaining.pop(key)
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        if key in TOP_LEVEL_BOOL_KEYS:
            v = value.lower()
            if v not in ("true", "false"):
                sys.exit(f"{key} must be true or false, got '{value}'.")
            lines[i] = f"{indent}{key.ljust(18)}= {v}\n"
        else:
            lines[i] = f'{indent}{key.ljust(18)}= "{esc(value)}"\n'
        changes.append((key, old_raw.strip().strip('"'), value))
    return changes


def update_parameter_overrides(lines: list[str], po_start, po_end, remaining: dict) -> list:
    """Update existing "Key=Value" entries in place. Pops handled keys out of `remaining`."""
    changes = []
    if not remaining or po_start is None:
        return changes
    for i in range(po_start, po_end + 1):
        m = re.match(r'^(\s*)"(\w+)=([^"]*)"(,?)\s*$', lines[i])
        if not m:
            continue
        indent, key, old_val, comma = m.groups()
        if key in remaining:
            value = remaining.pop(key)
            lines[i] = f'{indent}"{key}={esc(value)}"{comma}\n'
            changes.append((key, old_val, value))
    return changes


def append_new_parameter_overrides(lines: list[str], po_start, po_end, remaining: dict, environment: str) -> list:
    """Add any leftover keys as new parameter_overrides entries. Empties `remaining`."""
    if not remaining:
        return []
    if po_end is None:
        sys.exit(f"No parameter_overrides list in [{environment}.deploy.parameters] "
                  f"to add {list(remaining)} to.")
    indent = "  "
    for i in range(po_start, po_end):
        m = re.match(r'^(\s+)"', lines[i])
        if m:
            indent = m.group(1)
            break
    new_lines = [f'{indent}"{k}={esc(v)}",\n' for k, v in remaining.items()]
    lines[po_end:po_end] = new_lines
    changes = [(k, "(new)", v) for k, v in remaining.items()]
    remaining.clear()
    return changes


def print_changes(changes: list) -> None:
    print()
    for key, old, new in changes:
        if old == new:
            print(f"  {key}: unchanged ({new!r})")
        else:
            print(f"  {key}: {old!r} -> {new!r}")


def write_result(config_file: Path, environment: str, lines: list[str], changes: list, dry_run: bool) -> None:
    new_content = "".join(lines)
    try:
        tomllib.loads(new_content)
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"Refusing to write — result would not be valid TOML: {e}")

    changed = [c for c in changes if c[1] != c[2]]
    if dry_run:
        print(f"\n[dry-run] Would update {len(changed)} value(s) in [{environment}.deploy.parameters]. "
              f"No file written.")
    elif not changed:
        print(f"\n[{environment}.deploy.parameters] already matches AWS — nothing written.")
    else:
        with open(config_file, "w") as f:
            f.write(new_content)
        print(f"\nUpdated {len(changed)} value(s) in [{environment}.deploy.parameters] in {config_file}.")


def apply_updates(config_file: Path, environment: str, updates: dict, dry_run: bool):
    """Apply updates to samconfig.toml, editing only the matched lines."""
    with open(config_file) as f:
        lines = f.readlines()

    section_start, section_end = find_section_bounds(lines, environment)
    po_start, po_end = find_parameter_overrides_bounds(lines, section_start, section_end)

    remaining = dict(updates)
    changes = update_top_level_keys(lines, section_start, section_end, remaining)
    changes += update_parameter_overrides(lines, po_start, po_end, remaining)
    changes += append_new_parameter_overrides(lines, po_start, po_end, remaining, environment)

    print_changes(changes)
    write_result(config_file, environment, lines, changes, dry_run)


def main():
    environment, region_arg, dry_run = sys.argv[2], sys.argv[3], sys.argv[4] == "true"
    cli_overrides = dict(u.split("=", 1) for u in sys.argv[5:])
    config_file = resolve_config_path(sys.argv[1])

    with open(config_file, "rb") as f:
        cfg = tomllib.load(f)

    section = cfg.get(environment, {}).get("deploy", {}).get("parameters")
    region = region_arg or (section or {}).get("region") or "us-east-1"

    discovered = discover_cognito_values(environment, region)

    updates = {**discovered, **cli_overrides}  # explicit CLI values win over discovered ones
    if not updates:
        print(f"Nothing to update for {environment} — no pools found and no KEY=VALUE overrides given.")
        return

    apply_updates(config_file, environment, updates, dry_run)


if __name__ == "__main__":
    main()
