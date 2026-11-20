"""游戏 UI 的分层场景；仅绘制装饰，原图与等比缩放结果使用有界缓存。"""
import logging
from collections import OrderedDict
from pathlib import Path

import pygame

import physics

_ROOT = Path(__file__).resolve().parent.parent / "assets" / "ui" / "scenery"
_FILES = {name: _ROOT / f"scene_{name}.png" for name in
          ("sky", "mountains", "stands", "turf_soil")}
_sources = {}
_scaled = OrderedDict()
_log = logging.getLogger(__name__)


def _source(name):
    if name not in _sources:
        try:
            _sources[name] = pygame.image.load(str(_FILES[name])).convert_alpha()
        except (OSError, pygame.error) as exc:
            _log.warning("场景素材无法加载，回退程序绘制：%s (%s)", _FILES[name], exc)
            _sources[name] = None
    return _sources[name]


def _tile(name, height):
    key = (name, max(1, round(height)))
    if key in _scaled:
        _scaled.move_to_end(key)
        return _scaled[key]
    src = _source(name)
    if src is None:
        return None
    h = key[1]
    w = max(1, round(src.get_width() * h / src.get_height()))
    image = pygame.transform.smoothscale(src, (w, h))
    # 仅调整装饰层的 RGB，保留透明轮廓，让前景人物保持更高对比度。
    haze, amount = {
        "sky": ((228, 240, 250), 0.22),
        "mountains": ((220, 234, 241), 0.28),
        "stands": ((222, 234, 242), 0.36),
        "turf_soil": ((230, 215, 191), 0.25),
    }[name]
    keep = round(255 * (1 - amount))
    image.fill((keep, keep, keep), special_flags=pygame.BLEND_RGB_MULT)
    image.fill(tuple(round(c * amount) for c in haze), special_flags=pygame.BLEND_RGB_ADD)
    # 镜像交替使每个接点共享同一边缘，避免生成素材边缘有细微色差。
    pair = pygame.Surface((w * 2, h), pygame.SRCALPHA)
    pair.blit(image, (0, 0))
    pair.blit(pygame.transform.flip(image, True, False), (w, 0))
    _scaled[key] = pair
    while len(_scaled) > 1 and (len(_scaled) > 16 or
            sum(s.get_width() * s.get_height() * 4 for s in _scaled.values()) > 48 * 1024 * 1024):
        _scaled.popitem(last=False)
    return pair


def background(surf, rect, cam_px, ox, oy):
    """三层完整才接管，缺图时调用方回退旧场景。"""
    if any(_source(n) is None for n in ("sky", "mountains", "stands")):
        return False
    for name, height, bottom, factor in (
            ("sky", rect.h, 1.0, 0.025),
            ("mountains", rect.h * 0.72, 0.82, 0.08),
            ("stands", rect.h * 0.54, 0.82, 0.25)):
        tile = _tile(name, height)
        x = rect.x - round(cam_px * factor - ox * factor) % tile.get_width()
        y = rect.y + round(rect.h * bottom - tile.get_height() + oy * factor)
        while x < rect.right:
            surf.blit(tile, (x, y))
            x += tile.get_width()
        if name == "sky":
            pygame.draw.rect(surf, (139, 176, 154),
                             (rect.x, rect.y + round(rect.h * 0.70), rect.w,
                              rect.h - round(rect.h * 0.70)))
    return True


def terrain(surf, x0, scale, yt, track):
    """仅在地面块首次生成时按世界坐标贴合地形，上层刻度和障碍仍由原绘制器画。"""
    tile = _tile("turf_soil", scale * 1.35)
    if tile is None:
        return
    width, height = tile.get_size()
    world_origin = round(x0 * scale)
    for x in range(surf.get_width()):
        world_x = x0 + x / scale
        top = round((yt - physics.terrain_height(world_x, track)) * scale)
        tx = (world_origin + x) % width
        start = max(0, top)
        depth = max(0, start - top)
        if start < surf.get_height():
            # 纹理深度不足时延续末行颜色，避免竖向重复草根。
            surf.fill(tile.get_at((tx, height - 1)), (x, start, 1, surf.get_height() - start))
            count = min(height - depth, surf.get_height() - start)
            if count > 0:
                surf.blit(tile, (x, start), (tx, depth, 1, count))
