"""Render sample answers and menus to PNG files for a visual check.

    PYTHONPATH=. bridge/.venv/bin/python -m bridge.render_samples <output directory>

Every sample is written twice: ``<name>.png`` is the rendered image and
``<name>.4bpp.png`` is what the calculator shows after the image went through
a 4 bpp BLOCK payload.  The table printed at the end lists the image size,
the raw 4 bpp size and the size of the encoded BLOCK payload.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

try:
    from . import imagecodec, render
except ImportError:  # direct `python bridge/render_samples.py`
    import imagecodec
    import render

ENGLISH = """\
The **quadratic formula** solves $ax^2 + bx + c = 0$ for any $a \\neq 0$. \
The discriminant $\\Delta = b^2 - 4ac$ tells how many real roots exist: two when \
$\\Delta > 0$, one when $\\Delta = 0$ and none when $\\Delta < 0$. For example \
$x^2 - 5x + 6 = 0$ has $\\Delta = 1$, so $x = \\frac{5 \\pm 1}{2}$, that is $x_1 = 2$ and $x_2 = 3$.

Euler's identity $e^{i\\pi} + 1 = 0$ links five constants, and the area of a circle is \
$A = \\pi r^2$ while its circumference is $C = 2\\pi r$.
"""

CHINESE = """\
## 一元二次方程的求根公式

对于方程 $ax^2+bx+c=0$（其中 $a \\neq 0$），先把两边同时除以 $a$，再进行配方：

$$
\\begin{aligned}
x^2 + \\frac{b}{a}x &= -\\frac{c}{a} \\\\
\\left(x + \\frac{b}{2a}\\right)^2 &= \\frac{b^2 - 4ac}{4a^2} \\\\
x + \\frac{b}{2a} &= \\pm\\frac{\\sqrt{b^2 - 4ac}}{2a}
\\end{aligned}
$$

于是得到求根公式：

$$x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$$

其中判别式 $\\Delta = b^2-4ac$ 决定了根的情况：当 $\\Delta>0$ 时有两个不相等的实数根；当 $\\Delta=0$ 时有两个相等的实数根；当 $\\Delta<0$ 时没有实数根（但有一对共轭复根）。

### 其他常用公式

定积分与求和：

$$\\int_0^1 x^2\\,dx = \\frac{1}{3}, \\qquad \\sum_{k=1}^{n} k = \\frac{n(n+1)}{2}$$

重要极限：

$$\\lim_{x \\to 0} \\frac{\\sin x}{x} = 1$$

二阶矩阵的行列式与逆矩阵：

$$A = \\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}, \\quad \\det A = ad - bc$$

$$A^{-1} = \\frac{1}{ad-bc} \\begin{bmatrix} d & -b \\\\ -c & a \\end{bmatrix}$$

分段函数：

$$|x| = \\begin{cases} x, & x \\ge 0 \\\\ -x, & x < 0 \\end{cases}$$
"""

LIST_AND_CODE = """\
### 解题步骤 Steps

1. **移项**：把常数项移到等号右边
2. **配方**：两边同时加上 $\\left(\\frac{b}{2a}\\right)^2$
   - 左边成为完全平方
   - 右边通分，得到 `b*b - 4*a*c`
3. **开方**并整理，注意 *正负号*

用 Python 验证：

```python
import math

def solve(a, b, c):
    d = b * b - 4 * a * c  # 判别式
    if d < 0:
        return None
    r = math.sqrt(d)
    return (-b + r) / (2 * a), (-b - r) / (2 * a)

print(solve(1, -5, 6))  # a long line that needs to wrap at the character level
```

> 提示：当 $a = 0$ 时方程退化为一次方程。
"""

TABLE = """\
判别式与根的关系：

| 判别式 | 根的个数 | 根 |
|:---:|:---:|---|
| $\\Delta > 0$ | 2 | $x_{1,2} = \\frac{-b \\pm \\sqrt{\\Delta}}{2a}$ |
| $\\Delta = 0$ | 1 | $x = -\\frac{b}{2a}$ |
| $\\Delta < 0$ | 0 | 无实数根 |

A table that is too wide for the screen falls back to records:

| Function | Derivative | Integral | Domain | Notes |
|---|---|---|---|---|
| $\\sin x$ | $\\cos x$ | $-\\cos x + C$ | all real numbers | periodic with period $2\\pi$ |
| $\\ln x$ | $\\frac{1}{x}$ | $x\\ln x - x + C$ | $x > 0$ | inverse of $e^x$ |
"""

WIDE_MATH = """\
过宽的公式会先缩小，仍然放不下时在运算符处换行：

$$(a+b)^5 = a^5 + 5a^4b + 10a^3b^2 + 10a^2b^3 + 5ab^4 + b^5 = \\sum_{k=0}^{5} \\binom{5}{k} a^{5-k} b^{k}$$

$$f(x) = a_0 + a_1 x + a_2 x^2 + a_3 x^3 + a_4 x^4 + a_5 x^5 + a_6 x^6 + a_7 x^7 + a_8 x^8 + a_9 x^9$$

无法解析的公式显示源码：$\\frac{1}{$，其余内容不受影响。最终答案：$\\boxed{x = 2}$
"""

USER_TURN = "[factorize] x^2 - 5x + 6，并解释每一步 $\\frac{a}{b}$ why does it work?"

INFO = "Session 3 · deepseek-v4-pro · thinking: high · 已切换到新会话"

MENU_TITLE = "Math 数学"
MENU_ITEMS = [
    ("1", "Explain 解释"),
    ("2", "Solve 求解"),
    ("3", "Simplify 化简"),
    ("4", "Factorize 因式分解"),
    ("5", "Expand 展开"),
    ("6", "Differentiate 求导"),
    ("7", "Integrate 积分"),
    ("8", "Limit 极限"),
    ("9", "Evaluate numerically"),
    ("0", "More… 下一页"),
    ("a", "Step-by-step 逐步推导"),
    ("b", "Check my answer 检查答案"),
]
MENU_FOOTER = "number = choose · 0 = next page · esc = close"

SESSION_ITEMS = [
    ("1", "● 一元二次方程的求根公式推导过程以及判别式"),
    ("2", "What is the derivative o"),
    ("3", "New chat"),
    ("9", "New chat 新会话"),
    ("0", "Delete current 删除当前"),
]


def samples(cfg: "render.RenderConfig") -> list[tuple[str, int, object]]:
    """(name, block kind, image) for every sample."""
    menu_cfg = render.RenderConfig(**{**cfg.__dict__, "width": 312})
    return [
        ("english", imagecodec.KIND_ASSISTANT, render.render_markdown(ENGLISH, cfg)),
        ("chinese_math", imagecodec.KIND_ASSISTANT, render.render_markdown(CHINESE, cfg)),
        ("list_code", imagecodec.KIND_ASSISTANT, render.render_markdown(LIST_AND_CODE, cfg)),
        ("table", imagecodec.KIND_ASSISTANT, render.render_markdown(TABLE, cfg)),
        ("wide_math", imagecodec.KIND_ASSISTANT, render.render_markdown(WIDE_MATH, cfg)),
        ("user_turn", imagecodec.KIND_USER, render.render_user_turn(USER_TURN, cfg)),
        ("info", imagecodec.KIND_INFO, render.render_info(INFO, cfg)),
        ("menu", imagecodec.KIND_ASSISTANT,
         render.render_menu(MENU_TITLE, MENU_ITEMS, cfg, footer=MENU_FOOTER, height_limit=222)),
        ("menu_312", imagecodec.KIND_ASSISTANT,
         render.render_menu(MENU_TITLE, MENU_ITEMS[:10], menu_cfg, footer=MENU_FOOTER, height_limit=220)),
        ("menu_sessions", imagecodec.KIND_ASSISTANT,
         render.render_menu("Chats", SESSION_ITEMS, menu_cfg, footer="page 1/1 · esc = close", height_limit=220)),
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    output = Path(argv[1])
    output.mkdir(parents=True, exist_ok=True)
    cfg = render.RenderConfig.from_env()
    for role, description in render.describe_fonts(cfg).items():
        if role != "fallbacks":
            print(f"font {role}: {description}")
    print()
    print(f"{'sample':<14} {'size':>9} {'raw 4bpp':>9} {'BLOCK':>7} {'ratio':>6} {'ms':>6}")
    total_raw = total_block = 0
    started = time.perf_counter()
    rendered = samples(cfg)
    elapsed = (time.perf_counter() - started) * 1000
    for name, kind, image in rendered:
        tick = time.perf_counter()
        payload = imagecodec.encode_block(kind, image, 1, bpp=4)
        restored = imagecodec.decode_block(payload)[5]
        raw = imagecodec.row_bytes(image.width, 4) * image.height
        image.save(output / f"{name}.png")
        restored.save(output / f"{name}.4bpp.png")
        restored.resize((image.width * 2, image.height * 2)).save(output / f"{name}.4bpp.2x.png")
        total_raw += raw
        total_block += len(payload)
        took = (time.perf_counter() - tick) * 1000
        size = f"{image.width}x{image.height}"
        print(f"{name:<14} {size:>9} {raw:>9} {len(payload):>7} {len(payload) / raw:>6.0%} {took:>6.1f}")
    print(f"{'total':<14} {'':>9} {total_raw:>9} {total_block:>7} {total_block / total_raw:>6.0%}")
    print(f"\nrendering all samples took {elapsed:.0f} ms")
    menu = rendered[7][2]
    keys = [imagecodec.ScreenKey(key=key, action=imagecodec.ACTION_CLOSE) for key, _label in MENU_ITEMS]
    screen = imagecodec.encode_screen(1, imagecodec.FLAG_SHOW, keys, menu, bpp=4)
    print(f"menu as a SCREEN payload with {len(keys)} keys: {len(screen)} bytes")
    print(f"files written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
