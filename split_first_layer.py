import copy
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import List, TypeVar, Iterable, Literal, Union

import click

T = TypeVar("T")
PositioningType = Literal["rel", "abs", "unset"]

layer_pattern = re.compile(";LAYER:(\d+)")
type_pattern = re.compile(";TYPE:(\w+)")

DEFAULT_TYPE = "unset"
DEFAULT_POSITIONING: PositioningType = "unset"


def first_or_none(ls: Iterable[T]) -> T:
    try:
        return next(iter(ls))
    except StopIteration:
        return None


def get_opcode(line: str) -> str:
    opcode = line.split()[0] if line else []
    return opcode if not opcode.startswith(";") else ";"


@dataclass
class Segment:
    layer: Union[int, Literal["unset"]]
    type: str
    start_e_position: int
    positioning_type: PositioningType = DEFAULT_POSITIONING
    extruder_positioning_type: PositioningType = DEFAULT_POSITIONING
    lines: List[str] = field(default_factory=list, repr=False)

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


def parse_gcode(gcode: List[str]) -> List[Segment]:
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

    for line in gcode:
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


def to_gcode(segments: List[Segment]) -> List[str]:
    return [line for segment in segments for line in segment.lines]


@click.command()
@click.argument("input_path", type=str)
@click.option("--keep-skirt", is_flag=True, help="keep skirt in both outputs")
def main(input_path: str, keep_skirt: bool):
    input_path = Path(input_path)
    gcode = input_path.read_text().splitlines()
    main_segments = parse_gcode(gcode)
    first_layer_segments = []
    for segment in main_segments:
        if segment.layer == "unset":
            first_layer_segments.append(copy.deepcopy(segment))

        if segment.layer == 0:
            first_layer_segments.append(copy.deepcopy(segment))
            if not keep_skirt or segment.type != "SKIRT":
                print(f"removing {segment}")
                segment.lines = [f"G92 E{segment.last_e_position}"]
        else:
            print(f"keeping {segment}")

    output = input_path.with_stem(f"{input_path.stem}_other_layers")
    output.write_text("\n".join(to_gcode(main_segments)))

    output = input_path.with_stem(f"{input_path.stem}_first_layer")
    output.write_text("\n".join(to_gcode(first_layer_segments)))


if __name__ == "__main__":
    main()
