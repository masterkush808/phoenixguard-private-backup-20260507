from typing import Any, Protocol, Sequence

ADAPTIVE_THRESH_GAUSSIAN_C: int
CC_STAT_LEFT: int
CC_STAT_TOP: int
CC_STAT_WIDTH: int
CC_STAT_HEIGHT: int
CC_STAT_AREA: int
CHAIN_APPROX_SIMPLE: int
COLOR_BGR2GRAY: int
COLOR_BGR2RGB: int
COLOR_HSV2RGB: int
COLOR_LAB2RGB: int
COLOR_RGB2BGR: int
COLOR_RGB2GRAY: int
COLOR_RGB2HSV: int
COLOR_RGB2LAB: int
DISOPTICAL_FLOW_PRESET_FAST: int
FONT_HERSHEY_COMPLEX_SMALL: int
FONT_HERSHEY_DUPLEX: int
FONT_HERSHEY_SIMPLEX: int
FONT_HERSHEY_TRIPLEX: int
IMWRITE_JPEG_QUALITY: int
INTER_AREA: int
INTER_LINEAR: int
INTER_NEAREST: int
LINE_AA: int
MORPH_CLOSE: int
MORPH_ELLIPSE: int
MORPH_OPEN: int
NORM_MINMAX: int
RETR_EXTERNAL: int
THRESH_BINARY: int
THRESH_OTSU: int


class _CLAHE(Protocol):
    def apply(self, src: Any, /) -> Any: ...


def adaptiveThreshold(
    src: Any,
    maxValue: float,
    adaptiveMethod: int,
    thresholdType: int,
    blockSize: int,
    C: float,
) -> Any: ...
def addWeighted(
    src1: Any,
    alpha: float,
    src2: Any,
    beta: float,
    gamma: float,
    dst: Any | None = ...,
    dtype: int = ...,
) -> Any: ...
def arcLength(curve: Any, closed: bool) -> float: ...
def boundingRect(array: Any) -> tuple[int, int, int, int]: ...
def Canny(
    image: Any,
    threshold1: float,
    threshold2: float,
    edges: Any | None = ...,
    apertureSize: int = ...,
    L2gradient: bool = ...,
) -> Any: ...
def cartToPolar(x: Any, y: Any, angleInDegrees: bool = ...) -> tuple[Any, Any]: ...
def connectedComponentsWithStats(
    image: Any,
    labels: Any | None = ...,
    stats: Any | None = ...,
    centroids: Any | None = ...,
    connectivity: int = ...,
    ltype: int = ...,
) -> tuple[int, Any, Any, Any]: ...
def contourArea(contour: Any, oriented: bool = ...) -> float: ...
def convexHull(
    points: Any,
    hull: Any | None = ...,
    clockwise: bool = ...,
    returnPoints: bool = ...,
) -> Any: ...
def countNonZero(src: Any) -> int: ...
def createCLAHE(clipLimit: float = ..., tileGridSize: tuple[int, int] = ...) -> _CLAHE: ...
def cvtColor(src: Any, code: int, dst: Any | None = ..., dstCn: int = ...) -> Any: ...
def dilate(
    src: Any,
    kernel: Any,
    dst: Any | None = ...,
    anchor: tuple[int, int] = ...,
    iterations: int = ...,
    borderType: int = ...,
    borderValue: Any = ...,
) -> Any: ...
def drawContours(
    image: Any,
    contours: Sequence[Any],
    contourIdx: int,
    color: Any,
    thickness: int = ...,
    lineType: int = ...,
    hierarchy: Any | None = ...,
    maxLevel: int = ...,
    offset: tuple[int, int] = ...,
) -> Any: ...
def findContours(
    image: Any,
    mode: int,
    method: int,
    offset: tuple[int, int] = ...,
) -> tuple[list[Any], Any]: ...
def fitEllipse(points: Any) -> tuple[tuple[float, float], tuple[float, float], float]: ...
def getStructuringElement(
    shape: int,
    ksize: tuple[int, int],
    anchor: tuple[int, int] = ...,
) -> Any: ...
def getTextSize(
    text: str,
    fontFace: int,
    fontScale: float,
    thickness: int,
) -> tuple[tuple[int, int], int]: ...
def HoughLinesP(
    image: Any,
    rho: float,
    theta: float,
    threshold: int,
    lines: Any | None = ...,
    minLineLength: float = ...,
    maxLineGap: float = ...,
) -> Any: ...
def imencode(ext: str, img: Any, params: Sequence[int] | None = ...) -> tuple[bool, Any]: ...
def moments(array: Any, binaryImage: bool = ...) -> dict[str, float]: ...
def morphologyEx(
    src: Any,
    op: int,
    kernel: Any,
    dst: Any | None = ...,
    anchor: tuple[int, int] = ...,
    iterations: int = ...,
    borderType: int = ...,
    borderValue: Any = ...,
) -> Any: ...
def normalize(
    src: Any,
    dst: Any | None,
    alpha: float = ...,
    beta: float = ...,
    norm_type: int = ...,
    dtype: int = ...,
    mask: Any | None = ...,
) -> Any: ...
def putText(
    img: Any,
    text: str,
    org: tuple[int, int],
    fontFace: int,
    fontScale: float,
    color: Any,
    thickness: int = ...,
    lineType: int = ...,
    bottomLeftOrigin: bool = ...,
) -> Any: ...
def rectangle(
    img: Any,
    pt1: tuple[int, int],
    pt2: tuple[int, int],
    color: Any,
    thickness: int = ...,
    lineType: int = ...,
    shift: int = ...,
) -> Any: ...
def resize(
    src: Any,
    dsize: tuple[int, int],
    dst: Any | None = ...,
    fx: float = ...,
    fy: float = ...,
    interpolation: int = ...,
) -> Any: ...
