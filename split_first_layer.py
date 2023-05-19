import re
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path
from typing import TypeVar, Iterable, Literal

import click

T = TypeVar("T")
PositioningType = Literal["rel", "abs", "unset"]

layer_pattern = re.compile(";LAYER:(\d+)")
type_pattern = re.compile(";TYPE:(\w+)")

DEFAULT_TYPE: str = "unset"
DEFAULT_POSITIONING: PositioningType = "unset"


def first_or_none(ls: Iterable[T]) -> T:
    try:
        return next(iter(ls))
    except StopIteration:
        return None


def get_opcode(line: str) -> str | None:
    if not line:
        # TODO: improve
        return None
    opcode = line.split()[0]
    return opcode if not opcode.startswith(";") else ";"


@dataclass
class Segment:
    layer: int | Literal["unset"]
    type: str
    start_e_position: int
    positioning_type: PositioningType = DEFAULT_POSITIONING
    extruder_positioning_type: PositioningType = DEFAULT_POSITIONING
    lines: list[str] = field(default_factory=list, repr=False)

    @cached_property
    def last_e_position(self):
        for line in self.lines[::-1]:
            if line and line.split()[0] not in ("G0", "G1"):
                continue

            if e_pos := first_or_none(
                float(pos[1:]) for pos in line.split()[1:] if pos.startswith("E")
            ):
                if self.extruder_positioning_type == "rel":
                    return self.start_e_position + e_pos
                if (
                    self.extruder_positioning_type == "unset"
                    and self.positioning_type == "rel"
                ):
                    return self.start_e_position + e_pos
                return e_pos  # default is absolute
        return self.start_e_position


def parse_gcode(gcode: str) -> list[Segment]:
    segments = [Segment(layer="unset", type=DEFAULT_TYPE, start_e_position=0)]
    layer = "unset"
    positioning_type: PositioningType = DEFAULT_POSITIONING
    extruder_positioning_type: PositioningType = DEFAULT_POSITIONING
    segment_type = DEFAULT_TYPE

    def open_new_segment() -> None:
        segments.append(
            Segment(
                layer=layer,
                type=segment_type,
                positioning_type=positioning_type,
                extruder_positioning_type=extruder_positioning_type,
                start_e_position=segments[-1].last_e_position,
            )
        )

    for line in gcode.splitlines():
        if match := layer_pattern.match(line):
            layer = int(match.group(1))
            segment_type = DEFAULT_TYPE  # reset type
            open_new_segment()

        if match := type_pattern.match(line):
            segment_type = match.group(1)
            open_new_segment()

        if get_opcode(line) == "M82":
            extruder_positioning_type = "abs"
            open_new_segment()

        if get_opcode(line) == "M83":
            extruder_positioning_type = "rel"
            open_new_segment()

        if get_opcode(line) == "G90":
            positioning_type = "abs"
            extruder_positioning_type = "unset"
            open_new_segment()

        if get_opcode(line) == "G91":
            positioning_type = "rel"
            extruder_positioning_type = "unset"
            open_new_segment()

        # turn off sequence start
        # TODO: I used startswith because a comment can follow, use better solution
        if line.startswith("M140 S0"):
            layer = "unset"
            open_new_segment()

        segments[-1].lines.append(line)
    return segments


def to_gcode(segments: list[Segment]) -> str:
    return "\n".join(line for segment in segments for line in segment.lines)


def format_attrs(*, layer_start: int, layer_end: int, with_skirt: bool) -> str:
    attrs: str = (
        f"_layer_{layer_start}"
        if layer_start == layer_end
        else f"_layers_{layer_start}_to_{layer_end}"
    )
    if with_skirt:
        attrs += f"_with_skirt"
    return attrs


@click.command()
@click.argument(
    "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "-s", "--split-at", type=int, required=True, help="the layer to split after"
)
@click.option(
    "--keep-skirt", is_flag=True, default=False, help="keep skirt in both outputs"
)
def main(input_path: Path, keep_skirt: bool, split_at: int):
    segments = parse_gcode(input_path.read_text())
    first_print_segments = []
    second_print_segments = []

    for segment in segments:
        if segment.layer == "unset":
            # put start and stop sequences in both outputs
            first_print_segments.append(replace(segment))
            second_print_segments.append(replace(segment))
        elif keep_skirt and segment.type == "SKIRT":
            # put skirt in both outputs if configured
            first_print_segments.append(replace(segment))
            second_print_segments.append(replace(segment))
        elif segment.layer <= split_at:
            # put in first print, in second print set the current E position to the segments end e position so
            # that the next move instruction won't have to over extrude the plastic to get to that position.
            first_print_segments.append(replace(segment))
            second_print_segments.append(
                replace(segment, lines=[f"G92 E{segment.last_e_position}"])
            )
        else:
            second_print_segments.append(replace(segment))

    max_segment = max(
        segment.layer for segment in second_print_segments if segment.layer != "unset"
    )

    output = input_path.with_stem(
        f"{input_path.stem}{format_attrs(layer_start=split_at + 1, layer_end=max_segment, with_skirt=keep_skirt)}"
    )
    output.write_text(to_gcode(first_print_segments))

    output = input_path.with_stem(
        f"{input_path.stem}{format_attrs(layer_start=0, layer_end=split_at, with_skirt=keep_skirt)}"
    )
    output.write_text(to_gcode(second_print_segments))


if __name__ == "__main__":
    main()
