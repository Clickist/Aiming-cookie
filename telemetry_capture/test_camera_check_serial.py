# -*- coding: utf-8 -*-
"""test_camera_check_serial.py — camera_probe.check_serial BP 子类校验回归自测（不需要游戏运行）。

背景（FORMAT.md §2.4-1）：本构建的活 PCM 实例常是原生子类（如 MetaGameplayCameraManager，
SuperStruct(+0x40) 一跳可达原生 PlayerCameraManager）。2026-08-30 前的 check_serial 只比对
原生 UClass(pcm_cls)——发现成功、逐帧校验恒 False → 全文件 null（实证 0830 文件 7900 null）。
修复后 check_serial 比对 cal["pcm_set"]（发现逻辑同一套 SuperStruct 链可达类族）。

本测试用最小 mock 进程（只实现 check_serial 用到的 u64 读）锁住三条语义：

  ① 子类实例 + pcm_set 含子类 → True（§2.4-1 的失败形态不再复现）
  ② 异类实例（槽位复用/换场景）→ False（粗防槽位复用的本义仍在）
  ③ 旧版 cal（无 pcm_set 字段）回退 {pcm_cls} 单类比对（向后兼容语义）

用法: python test_camera_check_serial.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import camera_probe


class _MockProc:
    """duck-type 进程：对象地址 → ClassPrivate 指针（check_serial 只读 obj+0x10）。"""

    def __init__(self, obj_cls_map):
        self._obj_cls = obj_cls_map

    def u64(self, addr):
        return self._obj_cls.get(addr)


def _cal(pcm_set, insts, pcm_cls=0x1110):
    return {"pcm_cls": pcm_cls, "pcm_set": pcm_set, "insts": insts}


def main():
    native = 0x1110      # 原生 PlayerCameraManager UClass
    subclass = 0x2220    # BP/引擎子类 UClass（如 MetaGameplayCameraManager）
    foreign = 0x3330     # 无关类（槽位被其他 actor 复用）
    inst1, inst2 = 0xAA00, 0xBB00

    # ① 子类实例 + 修复后的 cal（pcm_set 含子类）→ 必须 True
    p = _MockProc({inst1 + 0x10: subclass, inst2 + 0x10: subclass})
    cal = _cal({native, subclass}, [inst1, inst2])
    assert camera_probe.check_serial(p, cal, None) is True, \
        "§2.4-1 回归：子类实例在 pcm_set 族内必须判 True（修复前恒 False → 全文件 null）"

    # ② 异类实例 → False（粗防槽位复用语义保留）
    p = _MockProc({inst1 + 0x10: foreign, inst2 + 0x10: subclass})
    assert camera_probe.check_serial(p, cal, None) is False, \
        "异类实例必须判 False（防槽位复用）"

    # ③ 旧版 cal（无 pcm_set）回退单类比对：原生 True、子类 False
    legacy = {"pcm_cls": native, "insts": [inst1]}
    p = _MockProc({inst1 + 0x10: native})
    assert camera_probe.check_serial(p, legacy, None) is True, \
        "旧版 cal 回退 {pcm_cls}：原生实例判 True"
    p = _MockProc({inst1 + 0x10: subclass})
    assert camera_probe.check_serial(p, legacy, None) is False, \
        "旧版 cal 回退 {pcm_cls}：子类实例判 False（回退语义，不伪装成修复）"

    print("[ok] check_serial 三条语义全过：①子类族内 True ②异类 False ③旧版 cal 回退单类")


if __name__ == "__main__":
    main()
