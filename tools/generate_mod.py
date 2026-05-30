#!/usr/bin/env python3
import argparse
import copy
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

JsonObject = Dict[str, Any]


def load_json(path: Path) -> JsonObject:
    with path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise TypeError(f"Expected a JSON object in {path}")
    return data


def write_json(path: Path, data: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")


def normalize_rel_path(rel_path: str) -> Tuple[str, Path]:
    normalized = rel_path.replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part and part != "."]

    if not parts:
        raise ValueError("Relative path is empty.")
    if any(part == ".." for part in parts):
        raise ValueError(f"Relative path may not escape the mod folder: {rel_path}")

    return "/".join(parts), Path(*parts)


def possible_roots(pa_root: Path) -> List[Path]:
    root = pa_root.expanduser().resolve()
    roots: List[Path] = [root]

    if root.name.lower() not in {"media", "pa", "pa_ex1"}:
        roots.append(root / "media")
        roots.append(root / "PA" / "media")

    if root.name.lower() in {"bin_x64", "bin_x86"}:
        roots.append(root.parent / "media")
        roots.append(root.parent.parent / "media")

    unique_roots: List[Path] = []
    seen: Set[str] = set()
    for candidate in roots:
        key = str(candidate).lower()
        if key not in seen:
            unique_roots.append(candidate)
            seen.add(key)
    return unique_roots


def titan_aliases_for_rel_path(normalized_rel_path: str) -> List[Path]:
    parts = normalized_rel_path.split("/")
    aliases: List[Path] = []

    if len(parts) >= 3 and parts[0] == "pa" and parts[1] == "units":
        # TITANS expansion files may be stored physically under media/pa_ex1/units,
        # while their mounted in-game path is still /pa/units/...
        aliases.append(Path("pa_ex1", *parts[1:]))

    return aliases


def recursive_suffixes_for_rel_path(normalized_rel_path: str) -> List[str]:
    parts = normalized_rel_path.split("/")
    suffixes = [normalized_rel_path.lower()]

    if len(parts) >= 3 and parts[0] == "pa" and parts[1] == "units":
        suffixes.append("/".join(parts[1:]).lower())
        suffixes.append(("pa_ex1/" + "/".join(parts[1:])).lower())

    return suffixes


def find_by_suffix(search_root: Path, normalized_rel_path: str) -> Path | None:
    if not search_root.exists():
        return None

    filename = normalized_rel_path.rsplit("/", 1)[-1]
    suffixes = recursive_suffixes_for_rel_path(normalized_rel_path)

    matches: List[Path] = []
    for candidate in search_root.rglob(filename):
        if not candidate.is_file():
            continue
        candidate_posix = candidate.as_posix().lower()
        if any(candidate_posix.endswith(suffix) for suffix in suffixes):
            matches.append(candidate)

    if not matches:
        return None

    # Prefer a normal /pa/ hit if it exists, otherwise a /pa_ex1/ hit.
    matches.sort(key=lambda path: ("/pa_ex1/" in path.as_posix().lower(), len(path.as_posix())))
    return matches[0]


def resolve_base_file(pa_root: Path, rel_path: str) -> Path:
    normalized_rel_path, rel_parts = normalize_rel_path(rel_path)
    roots = possible_roots(pa_root)

    candidates: List[Path] = []
    for root in roots:
        candidates.append(root / rel_parts)
        for alias in titan_aliases_for_rel_path(normalized_rel_path):
            candidates.append(root / alias)

    unique_candidates: List[Path] = []
    seen_candidates: Set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen_candidates:
            unique_candidates.append(candidate)
            seen_candidates.add(key)

    for candidate in unique_candidates:
        if candidate.is_file():
            return candidate

    for root in roots:
        found = find_by_suffix(root, normalized_rel_path)
        if found is not None:
            return found

    checked = "\n".join(f"  - {candidate}" for candidate in unique_candidates)
    raise FileNotFoundError(
        f"Could not find base-game file for {normalized_rel_path}.\n"
        f"Check that --pa-root points to either the PA:TITANS install folder or its media folder.\n"
        f"For TITANS units, this generator also checks physical pa_ex1 paths.\n"
        f"Useful diagnostic command on PowerShell:\n"
        f"  Get-ChildItem -Path \"{pa_root}\" -Recurse -Filter \"{Path(normalized_rel_path).name}\" | Select-Object FullName\n"
        f"Checked direct paths:\n{checked}"
    )


def parse_path(dotted_path: str) -> List[Any]:
    parts: List[Any] = []
    for raw_part in dotted_path.split("."):
        if not raw_part:
            raise ValueError(f"Invalid empty path segment in {dotted_path!r}")
        if raw_part.isdigit():
            parts.append(int(raw_part))
        else:
            parts.append(raw_part)
    return parts


def get_nested(data: Any, dotted_path: str, optional: bool) -> Any:
    current = data
    for part in parse_path(dotted_path):
        if isinstance(part, int):
            if not isinstance(current, list) or part < 0 or part >= len(current):
                if optional:
                    return None
                raise KeyError(dotted_path)
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                if optional:
                    return None
                raise KeyError(dotted_path)
            current = current[part]
    return current


def set_nested(data: Any, dotted_path: str, value: Any) -> None:
    parts = parse_path(dotted_path)
    current = data

    for part in parts[:-1]:
        if isinstance(part, int):
            if not isinstance(current, list) or part < 0 or part >= len(current):
                raise KeyError(dotted_path)
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                raise KeyError(dotted_path)
            current = current[part]

    final_part = parts[-1]
    if isinstance(final_part, int):
        if not isinstance(current, list) or final_part < 0 or final_part >= len(current):
            raise KeyError(dotted_path)
        current[final_part] = value
    else:
        if not isinstance(current, dict) or final_part not in current:
            raise KeyError(dotted_path)
        current[final_part] = value


def normalize_number(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        rounded = round(value, 6)
        if abs(rounded - round(rounded)) < 0.000001:
            return int(round(rounded))
        return rounded
    return value


def apply_change(data: JsonObject, change: JsonObject) -> str:
    dotted_path = change["path"]
    optional = bool(change.get("optional", False))
    old_value = get_nested(data, dotted_path, optional)

    if old_value is None and optional:
        return f"SKIPPED optional missing path: {dotted_path}"

    operation = change["op"]
    if operation == "multiply":
        if not isinstance(old_value, (int, float)) or isinstance(old_value, bool):
            raise TypeError(f"Cannot multiply non-numeric value at {dotted_path}: {old_value!r}")
        new_value = normalize_number(float(old_value) * float(change["factor"]))
    elif operation == "set":
        new_value = change["value"]
    else:
        raise ValueError(f"Unsupported operation: {operation}")

    set_nested(data, dotted_path, new_value)
    return f"{dotted_path}: {old_value!r} -> {new_value!r}"


def ensure_safe_clean(output_dir: Path, clean: bool) -> None:
    if not clean:
        return

    resolved_output = output_dir.expanduser().resolve()
    resolved_cwd = Path.cwd().resolve()

    if resolved_output == resolved_cwd:
        raise ValueError(
            "Refusing to clean the current folder. Use --no-clean when generating in-place."
        )

    suspicious_children = ["tools", "balance_plan.json", ".git"]
    if output_dir.exists() and any((output_dir / child).exists() for child in suspicious_children):
        raise ValueError(
            f"Refusing to clean {output_dir} because it looks like a source/project folder. "
            "Choose a build folder, or use --no-clean if you deliberately want to write there."
        )


def make_zip(source_dir: Path, zip_path: Path, files_to_include: Iterable[Path]) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    unique_files: List[Path] = []
    seen: Set[str] = set()
    for relative_path in files_to_include:
        key = relative_path.as_posix().lower()
        if key in seen:
            continue
        seen.add(key)
        unique_files.append(relative_path)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for relative_path in sorted(unique_files, key=lambda path: path.as_posix()):
            file_path = source_dir / relative_path
            if file_path.is_file():
                if file_path.resolve() == zip_path.resolve():
                    continue
                zip_file.write(file_path, relative_path.as_posix())


def generate(plan_path: Path, pa_root: Path, output_dir: Path, zip_path: Path | None, clean: bool) -> None:
    plan = load_json(plan_path)
    actions = plan.get("actions", [])
    if not isinstance(actions, list):
        raise TypeError("balance_plan.json must contain an actions array.")

    ensure_safe_clean(output_dir, clean)

    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    files_to_include: List[Path] = []

    write_json(output_dir / "modinfo.json", plan["modinfo"])
    files_to_include.append(Path("modinfo.json"))

    summary_lines: List[str] = []
    for action in actions:
        rel_path = action["rel_path"]
        normalized_rel_path, output_rel_path = normalize_rel_path(rel_path)
        base_file = resolve_base_file(pa_root, normalized_rel_path)
        edited_data = copy.deepcopy(load_json(base_file))

        summary_lines.append(f"[{action.get('name', normalized_rel_path)}]")
        summary_lines.append(f"base: {base_file}")
        summary_lines.append(f"output: {normalized_rel_path}")

        for change in action["changes"]:
            change_summary = apply_change(edited_data, change)
            summary_lines.append(f"  {change_summary}")

        # Important: write the full current-game spec, not a self-referencing base_spec patch.
        write_json(output_dir / output_rel_path, edited_data)
        files_to_include.append(output_rel_path)
        summary_lines.append("")

    summary_path = output_dir / "generation-summary.txt"
    summary_path.write_text("\n".join(summary_lines).rstrip() + "\n", encoding="utf-8")
    files_to_include.append(Path("generation-summary.txt"))

    if zip_path is not None:
        make_zip(output_dir, zip_path, files_to_include)

    print(f"Generated mod folder: {output_dir}")
    if zip_path is not None:
        print(f"Generated zip: {zip_path}")
    print("Wrote full unit specs generated from the current base game files.")
    print("TITANS physical pa_ex1 files are supported while keeping /pa/... output paths.")


def main(argv: Iterable[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate the Tallboys PA:TITANS balance mod from current base-game JSON files.")
    parser.add_argument(
        "--pa-root",
        required=True,
        type=Path,
        help="Path to the PA:TITANS install folder or media folder. Example: D:/SteamLibrary/steamapps/common/Planetary Annihilation Titans/media",
    )
    parser.add_argument("--plan", type=Path, default=Path("balance_plan.json"), help="Path to balance_plan.json.")
    parser.add_argument("--output-dir", type=Path, default=Path("build/com.pa.lockyaw.tallboys"), help="Generated mod folder.")
    parser.add_argument("--zip", type=str, default="build/com.pa.lockyaw.tallboys.zip", help="Generated zip path. Use --zip none to skip zipping.")
    parser.add_argument("--no-clean", action="store_true", help="Do not remove the previous output directory before generating.")
    args = parser.parse_args(list(argv))

    zip_path = None if args.zip.lower() == "none" else Path(args.zip)
    generate(args.plan, args.pa_root, args.output_dir, zip_path, not args.no_clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
