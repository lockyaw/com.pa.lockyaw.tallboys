#!/usr/bin/env python3
import argparse
import copy
import json
import math
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


def split_units_suffix(normalized_path: str) -> str | None:
    parts = normalized_path.split("/")
    if len(parts) >= 3 and parts[1] == "units" and parts[0] in {"pa", "pa_ex1"}:
        return "/".join(parts[2:])
    return None


def possible_roots(pa_root: Path) -> List[Path]:
    root = pa_root.expanduser().resolve()
    roots: List[Path] = [root]

    root_name = root.name.lower()
    if root_name not in {"media", "pa", "pa_ex1"}:
        roots.append(root / "media")
        roots.append(root / "PA" / "media")

    if root_name in {"bin_x64", "bin_x86"}:
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


def direct_candidates(root: Path, normalized_source_path: str, prefer_pa_ex1_for_units: bool) -> List[Path]:
    parts = normalized_source_path.split("/")
    candidates: List[Path] = []

    is_pa_unit = len(parts) >= 3 and parts[0] == "pa" and parts[1] == "units"
    is_pa_ex1_unit = len(parts) >= 3 and parts[0] == "pa_ex1" and parts[1] == "units"

    if is_pa_unit and prefer_pa_ex1_for_units:
        # TITANS commonly stores expansion overrides under media/pa_ex1/units while the
        # in-game spec path remains /pa/units. Prefer that physical source when present.
        candidates.append(root / Path("pa_ex1", *parts[1:]))
        candidates.append(root / Path(*parts))
    elif is_pa_ex1_unit:
        candidates.append(root / Path(*parts))
        candidates.append(root / Path("pa", *parts[1:]))
    elif is_pa_unit:
        candidates.append(root / Path(*parts))
        candidates.append(root / Path("pa_ex1", *parts[1:]))
    else:
        candidates.append(root / Path(*parts))

    return candidates


def suffixes_for_source_path(normalized_source_path: str, prefer_pa_ex1_for_units: bool) -> List[str]:
    parts = normalized_source_path.split("/")
    suffixes: List[str] = []

    def add(value: str) -> None:
        lowered = value.lower()
        if lowered not in suffixes:
            suffixes.append(lowered)

    is_pa_unit = len(parts) >= 3 and parts[0] == "pa" and parts[1] == "units"
    is_pa_ex1_unit = len(parts) >= 3 and parts[0] == "pa_ex1" and parts[1] == "units"

    if is_pa_unit and prefer_pa_ex1_for_units:
        add("pa_ex1/" + "/".join(parts[1:]))
        add(normalized_source_path)
        add("/".join(parts[1:]))
    elif is_pa_unit:
        add(normalized_source_path)
        add("pa_ex1/" + "/".join(parts[1:]))
        add("/".join(parts[1:]))
    elif is_pa_ex1_unit:
        add(normalized_source_path)
        add("pa/" + "/".join(parts[1:]))
        add("/".join(parts[1:]))
    else:
        add(normalized_source_path)

    return suffixes


def find_by_suffix(search_root: Path, normalized_source_path: str, prefer_pa_ex1_for_units: bool) -> Path | None:
    if not search_root.exists():
        return None

    filename = normalized_source_path.rsplit("/", 1)[-1]
    suffixes = suffixes_for_source_path(normalized_source_path, prefer_pa_ex1_for_units)

    matches: List[Path] = []
    for candidate in search_root.rglob(filename):
        if not candidate.is_file():
            continue
        candidate_posix = candidate.as_posix().lower()
        if any(candidate_posix.endswith(suffix) for suffix in suffixes):
            matches.append(candidate)

    if not matches:
        return None

    def priority(path: Path) -> Tuple[int, int]:
        lowered = path.as_posix().lower()
        in_pa_ex1 = "/pa_ex1/" in lowered or lowered.endswith("/pa_ex1")
        wants_pa_ex1 = prefer_pa_ex1_for_units and normalized_source_path.startswith("pa/units/")
        if normalized_source_path.startswith("pa_ex1/units/"):
            wants_pa_ex1 = True
        return (0 if in_pa_ex1 == wants_pa_ex1 else 1, len(lowered))

    matches.sort(key=priority)
    return matches[0]


def resolve_base_file(pa_root: Path, source_rel_path: str, prefer_pa_ex1_for_units: bool) -> Path:
    normalized_source_path, _ = normalize_rel_path(source_rel_path)
    roots = possible_roots(pa_root)

    candidates: List[Path] = []
    for root in roots:
        candidates.extend(direct_candidates(root, normalized_source_path, prefer_pa_ex1_for_units))

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
        found = find_by_suffix(root, normalized_source_path, prefer_pa_ex1_for_units)
        if found is not None:
            return found

    checked = "\n".join(f"  - {candidate}" for candidate in unique_candidates)
    filename = Path(normalized_source_path).name
    nearby_matches: List[Path] = []
    for root in roots:
        if root.exists():
            nearby_matches.extend(sorted(root.rglob(filename))[:20])

    nearby_text = ""
    if nearby_matches:
        nearby_text = "\nFound same filename elsewhere:\n" + "\n".join(f"  - {path}" for path in nearby_matches[:20])

    raise FileNotFoundError(
        f"Could not find base-game source file for {normalized_source_path}.\n"
        f"Check that --pa-root points to either the PA:TITANS install folder or its media folder.\n"
        f"Useful diagnostic command on PowerShell:\n"
        f"  Get-ChildItem -Path \"{pa_root}\" -Recurse -Filter \"{filename}\" | Select-Object FullName\n"
        f"Checked direct paths:\n{checked}"
        f"{nearby_text}"
    )


def deep_merge(parent: Any, child: Any) -> Any:
    if isinstance(parent, dict) and isinstance(child, dict):
        merged = copy.deepcopy(parent)
        for key, child_value in child.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], child_value)
            else:
                merged[key] = copy.deepcopy(child_value)
        return merged
    return copy.deepcopy(child)


def should_resolve_base_from_pa_tree(current_file: Path, current_requested_path: str, base_spec: str) -> bool:
    base_normalized, _ = normalize_rel_path(base_spec)
    current_suffix = split_units_suffix(current_requested_path)
    base_suffix = split_units_suffix(base_normalized)
    if current_suffix is None or base_suffix is None or current_suffix != base_suffix:
        return False

    lowered = current_file.as_posix().lower()
    return "/pa_ex1/units/" in lowered or lowered.endswith("/pa_ex1/units")


def load_merged_spec(
    pa_root: Path,
    source_rel_path: str,
    prefer_pa_ex1_for_units: bool,
    summary_lines: List[str],
    stack: Tuple[str, ...] = (),
) -> Tuple[JsonObject, Path]:
    normalized_source_path, _ = normalize_rel_path(source_rel_path)
    stack_key = f"{normalized_source_path}|pa_ex1={prefer_pa_ex1_for_units}"
    if stack_key in stack:
        chain = " -> ".join(stack + (stack_key,))
        raise ValueError(f"Detected base_spec recursion: {chain}")

    source_file = resolve_base_file(pa_root, normalized_source_path, prefer_pa_ex1_for_units)
    data = load_json(source_file)
    base_spec = data.get("base_spec")

    if isinstance(base_spec, str):
        force_pa_tree = should_resolve_base_from_pa_tree(source_file, normalized_source_path, base_spec)
        child_without_base = copy.deepcopy(data)
        child_without_base.pop("base_spec", None)
        parent, _ = load_merged_spec(
            pa_root=pa_root,
            source_rel_path=base_spec,
            prefer_pa_ex1_for_units=False if force_pa_tree else prefer_pa_ex1_for_units,
            summary_lines=summary_lines,
            stack=stack + (stack_key,),
        )
        merged = deep_merge(parent, child_without_base)
        if force_pa_tree:
            summary_lines.append(
                f"  flattened pa_ex1 override over base spec {normalize_rel_path(base_spec)[0]}"
            )
        return merged, source_file

    return data, source_file


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


def set_or_add_nested(data: Any, dotted_path: str, value: Any) -> None:
    parts = parse_path(dotted_path)
    current = data

    for part in parts[:-1]:
        if isinstance(part, int):
            if not isinstance(current, list) or part < 0 or part >= len(current):
                raise KeyError(dotted_path)
            current = current[part]
        else:
            if not isinstance(current, dict):
                raise KeyError(dotted_path)
            if part not in current or current[part] is None:
                current[part] = {}
            current = current[part]

    final_part = parts[-1]
    if isinstance(final_part, int):
        if not isinstance(current, list) or final_part < 0 or final_part >= len(current):
            raise KeyError(dotted_path)
        current[final_part] = value
    else:
        if not isinstance(current, dict):
            raise KeyError(dotted_path)
        current[final_part] = value


def normalize_number(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return math.floor(value)
    if isinstance(value, list):
        return [normalize_number(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_number(item) for key, item in value.items()}
    return value


def apply_change(data: JsonObject, change: JsonObject) -> str:
    dotted_path = change["path"]
    operation = change["op"]
    optional = bool(change.get("optional", False))

    if operation == "set_or_add":
        old_value = get_nested(data, dotted_path, optional=True)
        new_value = normalize_number(change["value"])
        set_or_add_nested(data, dotted_path, new_value)
        if old_value is None:
            return f"{dotted_path}: added {new_value!r}"
        return f"{dotted_path}: {old_value!r} -> {new_value!r}"

    old_value = get_nested(data, dotted_path, optional)

    if old_value is None and optional:
        return f"SKIPPED optional missing path: {dotted_path}"

    if operation == "multiply":
        if not isinstance(old_value, (int, float)) or isinstance(old_value, bool):
            raise TypeError(f"Cannot multiply non-numeric value at {dotted_path}: {old_value!r}")
        new_value = normalize_number(float(old_value) * float(change["factor"]))
    elif operation == "set":
        new_value = normalize_number(change["value"])
    else:
        raise ValueError(f"Unsupported operation: {operation}")

    set_nested(data, dotted_path, new_value)
    return f"{dotted_path}: {old_value!r} -> {new_value!r}"


def remove_dangerous_self_base_spec(data: JsonObject, output_rel_path: str) -> str | None:
    base_spec = data.get("base_spec")
    if not isinstance(base_spec, str):
        return None

    base_normalized, _ = normalize_rel_path(base_spec)
    output_normalized, _ = normalize_rel_path(output_rel_path)
    if base_normalized == output_normalized:
        data.pop("base_spec", None)
        return f"removed self-referencing base_spec: {base_spec}"
    return None


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


def clean_generated_files(output_dir: Path) -> None:
    for relative in ["pa", "generation-summary.txt"]:
        target = output_dir / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


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

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path in sorted(unique_files, key=lambda value: value.as_posix().lower()):
            full_path = source_dir / relative_path
            if full_path.is_file():
                archive.write(full_path, relative_path.as_posix())


def plan_actions(plan: JsonObject) -> List[JsonObject]:
    raw_actions = plan.get("actions", plan.get("edits"))
    if not isinstance(raw_actions, list):
        raise ValueError("balance_plan.json requires an actions array.")
    return raw_actions


def validate_plan(plan: JsonObject) -> None:
    if "modinfo" not in plan or not isinstance(plan["modinfo"], dict):
        raise ValueError("balance_plan.json requires a modinfo object.")

    for index, edit in enumerate(plan_actions(plan)):
        if not isinstance(edit, dict):
            raise ValueError(f"action {index} must be an object.")
        if "rel_path" not in edit and ("source_rel_path" not in edit or "output_rel_path" not in edit):
            raise ValueError(
                f"action {index} requires rel_path, or both source_rel_path and output_rel_path."
            )
        if "changes" not in edit or not isinstance(edit["changes"], list):
            raise ValueError(f"action {index} requires a changes array.")


def generate(plan_path: Path, pa_root: Path, output_dir: Path, zip_path: Path | None, clean: bool, clean_generated: bool, prefer_pa_ex1_for_units: bool) -> None:
    plan = load_json(plan_path)
    validate_plan(plan)

    planned_json_files: List[Tuple[Path, JsonObject]] = []
    generated_files: List[Path] = []

    planned_json_files.append((Path("modinfo.json"), plan["modinfo"]))
    generated_files.append(Path("modinfo.json"))

    summary: List[str] = []
    errors: List[str] = []

    for edit in plan_actions(plan):
        source_rel_path = edit.get("source_rel_path", edit.get("rel_path"))
        output_rel_path = edit.get("output_rel_path", edit.get("rel_path"))
        action_name = str(edit.get("name", output_rel_path))

        if not isinstance(source_rel_path, str) or not isinstance(output_rel_path, str):
            errors.append(f"[{action_name}] rel_path/source_rel_path/output_rel_path must be strings.")
            continue

        try:
            normalized_output_path, output_parts = normalize_rel_path(output_rel_path)
            normalized_source_path, _ = normalize_rel_path(source_rel_path)

            local_summary: List[str] = []
            data, base_file = load_merged_spec(
                pa_root=pa_root,
                source_rel_path=normalized_source_path,
                prefer_pa_ex1_for_units=prefer_pa_ex1_for_units,
                summary_lines=local_summary,
            )

            summary.append(f"[{action_name}]")
            summary.append(f"source: {base_file}")
            summary.append(f"output: {normalized_output_path}")
            for line in local_summary:
                summary.append(line)

            for change in edit["changes"]:
                summary.append("  " + apply_change(data, change))

            base_spec_note = remove_dangerous_self_base_spec(data, normalized_output_path)
            if base_spec_note is not None:
                summary.append("  " + base_spec_note)

            planned_json_files.append((output_parts, data))
            generated_files.append(output_parts)
            summary.append("")
        except Exception as exception:
            errors.append(f"[{action_name}]\n{exception}")

    if errors:
        message = "\n\n".join(errors)
        raise RuntimeError(
            "Generation stopped before writing output because one or more actions failed.\n\n"
            f"{message}"
        )

    ensure_safe_clean(output_dir, clean)
    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if clean_generated:
        clean_generated_files(output_dir)

    for relative_path, data in planned_json_files:
        write_json(output_dir / relative_path, data)

    summary_path = output_dir / "generation-summary.txt"
    summary_path.write_text("\n".join(summary), encoding="utf-8")
    generated_files.append(Path("generation-summary.txt"))

    if zip_path is not None:
        make_zip(output_dir, zip_path, generated_files)


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the Tallboys PA:TITANS balance mod from the installed base-game JSON files."
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path("balance_plan.json"),
        help="Path to balance_plan.json. Defaults to ./balance_plan.json.",
    )
    parser.add_argument(
        "--pa-root",
        type=Path,
        required=True,
        help="Path to the PA:TITANS install folder or its media folder.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build/com.pa.lockyaw.tallboys"),
        help="Output mod folder. Defaults to build/com.pa.lockyaw.tallboys.",
    )
    parser.add_argument(
        "--zip",
        type=Path,
        default=Path("build/com.pa.lockyaw.tallboys.zip"),
        help="Output release zip. Use --zip \"\" to skip zip creation.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not delete the output folder before writing. Required when generating in-place.",
    )
    parser.add_argument(
        "--clean-generated",
        action="store_true",
        help="Delete previously generated pa/ files and generation-summary.txt before writing. Safe for in-place generation.",
    )
    parser.add_argument(
        "--classic-source",
        action="store_true",
        help="Prefer media/pa over media/pa_ex1 for /pa/units sources. Default is TITANS-style pa_ex1 preference.",
    )

    args = parser.parse_args(argv)
    raw_zip_path = str(args.zip).strip().strip('"')
    zip_path = None if raw_zip_path in {"", "."} else args.zip
    generate(
        plan_path=args.plan,
        pa_root=args.pa_root,
        output_dir=args.output_dir,
        zip_path=zip_path,
        clean=not args.no_clean,
        clean_generated=args.clean_generated,
        prefer_pa_ex1_for_units=not args.classic_source,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
