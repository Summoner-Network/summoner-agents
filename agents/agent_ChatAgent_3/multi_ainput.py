import sys, shutil
from aioconsole import ainput
from wcwidth import wcwidth

# ANSI helpers
_CURSOR_UP_FMT = "\x1b[{}A"
_CURSOR_DOWN_FMT = "\x1b[{}B"
_CLEAR_LINE = "\x1b[2K"

def _rows_used(prompt: str, text: str, tabsize: int = 8) -> int:
    """
    Accurately compute how many terminal rows the echoed line occupied,
    accounting for East-Asian wide chars, combining marks, and tabs.
    """
    cols = max(1, shutil.get_terminal_size((80, 20)).columns)

    def _advance(col: int, ch: str) -> int:
        if ch == "\t":
            # advance to next tab stop
            return col + (tabsize - (col % tabsize or 0 if tabsize == 0 else col % tabsize))
        w = wcwidth(ch)
        if w is None or w < 0:
            w = 0  # nonprintable/combining stays on same cell
        return col + w

    rows = 1
    col = 0

    # walk prompt
    for ch in prompt:
        new_col = _advance(col, ch)
        # wrap as needed
        if new_col >= cols:
            # number of wraps
            wraps = new_col // cols
            rows += wraps
            col = new_col % cols
        else:
            col = new_col

    # walk text
    for ch in text:
        new_col = _advance(col, ch)
        if new_col >= cols:
            wraps = new_col // cols
            rows += wraps
            col = new_col % cols
        else:
            col = new_col

    return rows

async def multi_ainput(
    prompt: str = "> ",
    cont_prompt: str = "~ ",
    sentinel: str = "\\",
) -> str:
    """
    Read one logical message; erase the trailing '\' from the echoed line even if it wrapped.
    """
    lines: list[str] = []
    current_prompt = prompt

    while True:
        line: str = await ainput(current_prompt)

        if line.endswith(sentinel):
            line_clean = line[:-1]
            lines.append(line_clean)

            # how many rows did the terminal use to echo "<prompt><line>"?
            rows = _rows_used(current_prompt, line)

            # move to the start of that echoed block
            sys.stdout.write(_CURSOR_UP_FMT.format(rows))
            # clear each physical row in that block
            for i in range(rows):
                sys.stdout.write("\r" + _CLEAR_LINE)
                if i < rows - 1:
                    sys.stdout.write(_CURSOR_DOWN_FMT.format(1))
            # return cursor to the top of the cleared block
            if rows > 1:
                sys.stdout.write(_CURSOR_UP_FMT.format(rows - 1))

            # redraw without the backslash and end with newline
            sys.stdout.write(f"{current_prompt}{line_clean}\n")
            sys.stdout.flush()

            current_prompt = cont_prompt
            continue

        lines.append(line)
        break

    return "\n".join(lines)

