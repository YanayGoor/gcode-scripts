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
mesh_pattern = re.compile(";MESH:(.*)")

DEFAULT_TYPE: str = "unset"
DEFAULT_MESH: str = "unset"
DEFAULT_POSITIONING: PositioningType = "unset"

MOV_OPCODES = {
    "G0",  # linear move
    "G1",  # linear move
    "G2",  # arc or circle move
    "G3",  # arc or circle move
}
CONTROL_OPCODES = {
    "M140",  # set bed temp
    "M104",  # set hotend temp
    "M105",  # report temp
    "M106",  # set fan speed
    "M109",  # wait for hotend temp
    "M190",  # wait for bed temp
    "G28",  # auto home
    "M107",  # fan off
    "M18",  # disable steppers
    "M84",  # disable steppers
    "G92",  # set position
}
META_OPCODES = {
    ";",  # comment
}


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


def get_args(line: str) -> list[str]:
    if not line:
        # TODO: improve
        return []
    return line.split()[1:]


@dataclass(frozen=True)
class Segment:
    layer: int | Literal["unset"]
    type: str
    mesh: str
    start_e_position: int
    start_z_position: int
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

    @cached_property
    def last_z_position(self):
        for line in self.lines[::-1]:
            if get_opcode(line) not in ("G0", "G1"):
                continue

            if z_pos := first_or_none(
                float(pos[1:]) for pos in get_args(line) if pos.startswith("Z")
            ):
                if self.positioning_type == "rel":
                    return self.start_z_position + z_pos
                return z_pos  # default is absolute
        return self.start_z_position

    @cached_property
    def control_lines(self) -> list[str]:
        return [
            line
            for line in self.lines
            if get_opcode(line) in CONTROL_OPCODES | META_OPCODES
        ]


def parse_gcode(gcode: str) -> list[Segment]:
    segments = [
        Segment(
            layer="unset",
            type=DEFAULT_TYPE,
            mesh=DEFAULT_MESH,
            start_e_position=0,
            start_z_position=0,
        )
    ]
    layer = "unset"
    positioning_type: PositioningType = DEFAULT_POSITIONING
    extruder_positioning_type: PositioningType = DEFAULT_POSITIONING
    segment_type = DEFAULT_TYPE
    segment_mesh = DEFAULT_MESH

    def open_new_segment() -> None:
        segments.append(
            Segment(
                layer=layer,
                type=segment_type,
                mesh=segment_mesh,
                positioning_type=positioning_type,
                extruder_positioning_type=extruder_positioning_type,
                start_e_position=segments[-1].last_e_position,
                start_z_position=segments[-1].last_z_position,
            )
        )

    for lineno, line in enumerate(gcode.splitlines()):
        if not line:
            segments[-1].lines.append(line)

        elif match := layer_pattern.match(line):
            layer = int(match.group(1))
            segment_type = DEFAULT_TYPE  # reset type
            open_new_segment()
            segments[-1].lines.append(line)

        elif match := type_pattern.match(line):
            segment_type = match.group(1)
            open_new_segment()
            segments[-1].lines.append(line)

        elif match := mesh_pattern.match(line):
            segment_mesh = match.group(1)
            segment_type = "unset"
            open_new_segment()
            segments[-1].lines.append(line)

        elif get_opcode(line) == "M82":
            extruder_positioning_type = "abs"
            open_new_segment()
            segments[-1].lines.append(line)

        elif get_opcode(line) == "M83":
            extruder_positioning_type = "rel"
            open_new_segment()
            segments[-1].lines.append(line)

        elif get_opcode(line) == "G90":
            positioning_type = "abs"
            extruder_positioning_type = "unset"
            open_new_segment()
            segments[-1].lines.append(line)

        elif get_opcode(line) == "G91":
            positioning_type = "rel"
            extruder_positioning_type = "unset"
            open_new_segment()
            segments[-1].lines.append(line)

        elif line.startswith("M140 S0"):  # turn off sequence start
            layer = "unset"
            open_new_segment()
            segments[-1].lines.append(line)

        elif get_opcode(line) in MOV_OPCODES | CONTROL_OPCODES | META_OPCODES:
            segments[-1].lines.append(line)

        else:
            raise Exception(f"unknown inst in line num {lineno}: {line!r}")
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
    prev_segment: Segment | None = None
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
            # add to first print, and add controls to second print, although we mainly care about G92 there.
            first_print_segments.append(replace(segment))
            second_print_segments.append(replace(segment, lines=segment.control_lines))

        elif prev_segment:
            second_print_segments.append(
                replace(
                    segment,
                    lines=[
                        f"G92 E{prev_segment.last_e_position}",  # in case previous segment was not added, don't over extrude
                        f"G0 Z{prev_segment.last_z_position}",  # in case previous segment was not added, don't print at wrong height
                        *segment.lines,
                    ],
                )
            )

        else:
            second_print_segments.append(replace(segment))

        prev_segment = segment

    max_segment = max(
        segment.layer for segment in second_print_segments if segment.layer != "unset"
    )

    output = input_path.with_stem(
        f"{input_path.stem}{format_attrs(layer_start=0, layer_end=split_at, with_skirt=True)}"
    )
    output.write_text(to_gcode(first_print_segments))

    output = input_path.with_stem(
        f"{input_path.stem}{format_attrs(layer_start=split_at + 1, layer_end=max_segment, with_skirt=keep_skirt)}"
    )
    output.write_text(to_gcode(second_print_segments))


if __name__ == "__main__":
    main()
