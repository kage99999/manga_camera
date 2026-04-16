# -*- coding: utf-8 -*-
# ファイル名：dolly.py
# 00漫画用Camera Position Manager
# 機能：ドリーズーム（Vertigo効果）統合
# 仕様：
# - 「有効」ON中、カメラ移動に合わせてレンズを自動調整し、ターゲットの見かけサイズを一定に保つ
# - UIは「焦点距離」の直下に配置（ui.py側）
# - 依頼により「基準をキャプチャ」ボタンと「オートキー（レンズ）」はUIに出さない
# 変更点（1.113）:
# - バージョン更新に追従
# - ドリーズーム機能自体の挙動は現状維持

import bpy
import mathutils

from bpy.props import (
    BoolProperty,
    FloatProperty,
    FloatVectorProperty,
    PointerProperty,
    StringProperty,
)

from bpy.types import (
    PropertyGroup,
)

# -----------------------------------------------------------------------------
# 内部：depsgraphハンドラ（レンズ自動調整）
# -----------------------------------------------------------------------------

_DZ_UPDATING = False  # 再帰ガード（depsgraph更新→レンズ変更→depsgraph…を防ぐ）


def _safe_distance(a, b):
    # 2点間距離（ベクトル長）
    return (a - b).length


def _normalized_or_fallback(vec, fallback):
    # ゼロベクトル対策つき正規化
    try:
        if vec.length > 1e-12:
            return vec.normalized()
    except Exception:
        pass
    try:
        if fallback.length > 1e-12:
            return fallback.normalized()
    except Exception:
        pass
    return mathutils.Vector((0.0, 0.0, 1.0))


def _capture_baseline(scene, camera_obj, target_obj):
    # 有効化した瞬間の状態を「基準」として記録する
    cam_loc = camera_obj.matrix_world.translation
    tgt_loc = target_obj.matrix_world.translation
    d0 = _safe_distance(cam_loc, tgt_loc)
    props = scene.mpm_dolly_props
    current_lens = float(camera_obj.data.lens)
    props.baseline_distance = float(d0)
    props.baseline_lens = current_lens
    props.last_auto_lens = current_lens
    props.lock_active = False
    props.lock_mode = ""
    _snapshot_free_pose(scene, camera_obj, target_obj)


def _snapshot_free_pose(scene, camera_obj, target_obj):
    # ロックしていない通常状態の「最後に安定していた姿勢」を保存する
    props = scene.mpm_dolly_props
    try:
        cam_loc = camera_obj.matrix_world.translation.copy()
        tgt_loc = target_obj.matrix_world.translation.copy()
        direction = _normalized_or_fallback(cam_loc - tgt_loc, mathutils.Vector((0.0, 0.0, 1.0)))
        props.last_free_direction = (float(direction.x), float(direction.y), float(direction.z))
        props.last_free_distance = float((cam_loc - tgt_loc).length)
        props.last_eval_cam_loc = (float(cam_loc.x), float(cam_loc.y), float(cam_loc.z))
        props.last_free_rot_mode = str(getattr(camera_obj, 'rotation_mode', 'XYZ'))
        if props.last_free_rot_mode == 'QUATERNION':
            q = camera_obj.rotation_quaternion.copy()
            props.last_free_rot_quat = (float(q.w), float(q.x), float(q.y), float(q.z))
        elif props.last_free_rot_mode == 'AXIS_ANGLE':
            aa = camera_obj.rotation_axis_angle
            props.last_free_rot_axis_angle = (float(aa[0]), float(aa[1]), float(aa[2]), float(aa[3]))
        else:
            e = camera_obj.rotation_euler.copy()
            props.last_free_rot_euler = (float(e.x), float(e.y), float(e.z))
    except Exception:
        pass


def _restore_locked_pose(scene, camera_obj, target_obj, locked_distance):
    # ロック突入前の向き・回転を維持したまま、距離だけロック距離へ戻す
    props = scene.mpm_dolly_props
    tgt_loc = target_obj.matrix_world.translation.copy()
    direction = _normalized_or_fallback(
        mathutils.Vector(props.last_free_direction),
        camera_obj.matrix_world.translation.copy() - tgt_loc,
    )

    try:
        camera_obj.matrix_world.translation = tgt_loc + direction * float(locked_distance)
    except Exception:
        pass

    saved_mode = str(getattr(props, 'last_free_rot_mode', '') or 'XYZ')
    try:
        camera_obj.rotation_mode = saved_mode
    except Exception:
        pass

    try:
        if saved_mode == 'QUATERNION':
            q = props.last_free_rot_quat
            camera_obj.rotation_quaternion = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
        elif saved_mode == 'AXIS_ANGLE':
            aa = props.last_free_rot_axis_angle
            camera_obj.rotation_axis_angle[0] = float(aa[0])
            camera_obj.rotation_axis_angle[1] = float(aa[1])
            camera_obj.rotation_axis_angle[2] = float(aa[2])
            camera_obj.rotation_axis_angle[3] = float(aa[3])
        else:
            e = props.last_free_rot_euler
            camera_obj.rotation_euler = (float(e[0]), float(e[1]), float(e[2]))
    except Exception:
        pass


def _apply_manual_lens_as_equivalent_dolly(scene, camera_obj, target_obj, requested_lens):
    # ON中に手で焦点距離を変えた時は、
    # 「基準を差し替える」のではなく、
    # 今のドリーズーム関係式に対応する等価距離へカメラを移動させる。
    props = scene.mpm_dolly_props

    d0 = float(props.baseline_distance)
    f0 = float(props.baseline_lens)
    if d0 <= 1e-8 or f0 <= 1e-8:
        _capture_baseline(scene, camera_obj, target_obj)
        d0 = float(props.baseline_distance)
        f0 = float(props.baseline_lens)
        if d0 <= 1e-8 or f0 <= 1e-8:
            return False

    # まずはレンズ上下限を反映
    desired_lens = max(float(props.lens_min), min(float(props.lens_max), float(requested_lens)))

    # ドリーズーム関係式 lens = f0 * (distance / d0) から等価距離を逆算
    desired_distance = d0 * (desired_lens / f0)

    # レンズ上下限に対応する到達可能距離範囲へクランプ
    min_allowed_distance = max(float(props.min_distance), d0 * (float(props.lens_min) / f0))
    max_allowed_distance = max(min_allowed_distance, d0 * (float(props.lens_max) / f0))
    desired_distance = max(min_allowed_distance, min(max_allowed_distance, desired_distance))

    # 距離側をクランプした結果に合わせて、最終レンズも関係式側へ揃える
    desired_lens = f0 * (desired_distance / d0)
    desired_lens = max(float(props.lens_min), min(float(props.lens_max), desired_lens))

    tgt_loc = target_obj.matrix_world.translation.copy()
    cam_loc = camera_obj.matrix_world.translation.copy()
    direction = _normalized_or_fallback(
        cam_loc - tgt_loc,
        mathutils.Vector(props.last_free_direction),
    )

    try:
        camera_obj.matrix_world.translation = tgt_loc + direction * float(desired_distance)
    except Exception:
        return False

    try:
        if getattr(camera_obj.data, "type", "PERSP") != 'PERSP':
            return False
        camera_obj.data.lens = float(desired_lens)
    except Exception:
        return False

    props.last_auto_lens = float(desired_lens)
    props.lock_active = False
    props.lock_mode = ""
    props.lock_distance = 0.0
    _snapshot_free_pose(scene, camera_obj, target_obj)
    return True





def _rotation_change_degrees(camera_obj, props):
    # 現在回転と、最後に安定していた回転との差分角度をざっくり返す
    try:
        mode = str(getattr(props, 'last_free_rot_mode', '') or getattr(camera_obj, 'rotation_mode', 'XYZ'))
        if mode == 'QUATERNION':
            base = mathutils.Quaternion((float(props.last_free_rot_quat[0]), float(props.last_free_rot_quat[1]), float(props.last_free_rot_quat[2]), float(props.last_free_rot_quat[3])))
            cur = camera_obj.rotation_quaternion.copy()
        elif mode == 'AXIS_ANGLE':
            aa = props.last_free_rot_axis_angle
            base = mathutils.Quaternion(mathutils.Vector((float(aa[1]), float(aa[2]), float(aa[3]))), float(aa[0]))
            cur_aa = camera_obj.rotation_axis_angle
            cur = mathutils.Quaternion(mathutils.Vector((float(cur_aa[1]), float(cur_aa[2]), float(cur_aa[3]))), float(cur_aa[0]))
        else:
            base = mathutils.Euler((float(props.last_free_rot_euler[0]), float(props.last_free_rot_euler[1]), float(props.last_free_rot_euler[2])), mode).to_quaternion()
            cur = camera_obj.rotation_euler.copy().to_quaternion()
        angle = base.rotation_difference(cur).angle
        return abs(angle) * 57.29577951308232
    except Exception:
        return 0.0

def _movement_is_dolly_like(scene, camera_obj, target_obj):
    # 直前フレームからの移動が、ドリーズーム軸に沿う前後移動っぽいかを簡易判定
    props = scene.mpm_dolly_props
    try:
        prev_loc = mathutils.Vector(props.last_eval_cam_loc)
    except Exception:
        return True

    curr_loc = camera_obj.matrix_world.translation.copy()
    delta = curr_loc - prev_loc
    if delta.length <= 1e-8:
        return True

    tgt_loc = target_obj.matrix_world.translation.copy()
    axis = _normalized_or_fallback(
        mathutils.Vector(props.last_free_direction),
        prev_loc - tgt_loc,
    )

    alignment = abs(delta.normalized().dot(axis))
    # かなり軸に沿っている時だけ「ズーム系移動」とみなす
    return alignment >= 0.72


def _update_lens(scene, camera_obj, target_obj):
    # 見かけサイズ ∝ lens / distance を一定にする：lens = lens0 * (distance / distance0)
    # レンズ最小/最大に対応する距離範囲を超えた場合は、その距離でロックする。
    # ロック中は look-at を毎フレーム再計算せず、ロック前の姿勢を維持する。
    props = scene.mpm_dolly_props

    cam_loc = camera_obj.matrix_world.translation.copy()
    tgt_loc = target_obj.matrix_world.translation.copy()
    offset = cam_loc - tgt_loc
    raw_distance = offset.length

    # 破綻防止：最小距離
    if raw_distance < props.min_distance:
        raw_distance = props.min_distance

    d0 = float(props.baseline_distance)
    f0 = float(props.baseline_lens)

    # 基準が無効なら再キャプチャ
    if d0 <= 1e-8 or f0 <= 1e-8:
        _capture_baseline(scene, camera_obj, target_obj)
        d0 = float(props.baseline_distance)
        f0 = float(props.baseline_lens)
        if d0 <= 1e-8:
            return

    # レンズ上下限から到達可能な距離範囲を求める
    min_allowed_distance = max(float(props.min_distance), d0 * (float(props.lens_min) / f0))
    max_allowed_distance = max(min_allowed_distance, d0 * (float(props.lens_max) / f0))

    # ロック中かどうかを、現在の生距離から判定して更新する。
    # 境界ぴったりで毎フレームロック/解除を繰り返すと、
    # 回転操作や軽い横移動でもプルプルしやすいので、少し遊び幅を持たせる。
    boundary_deadzone = max(0.003, min_allowed_distance * 0.01)
    boundary_release = max(boundary_deadzone * 2.0, 0.006)

    desired_lock_mode = ""
    locked_distance = raw_distance
    movement_is_dolly_like = _movement_is_dolly_like(scene, camera_obj, target_obj)
    if props.lock_active and props.lock_mode == "MIN":
        # いったんMINロックに入った後は、ズーム軸っぽく「離れる」操作の時だけ解除判定する。
        # 回転や横移動では解除しない。
        if movement_is_dolly_like and raw_distance > min_allowed_distance + boundary_release:
            desired_lock_mode = ""
        else:
            desired_lock_mode = "MIN"
            locked_distance = min_allowed_distance
    elif props.lock_active and props.lock_mode == "MAX":
        # いったんMAXロックに入った後は、ズーム軸っぽく「戻る」操作の時だけ解除判定する。
        # 回転や横移動では解除しない。
        if movement_is_dolly_like and raw_distance < max_allowed_distance - boundary_release:
            desired_lock_mode = ""
        else:
            desired_lock_mode = "MAX"
            locked_distance = max_allowed_distance
    else:
        # 新規ロック突入は、境界を少し明確に超えた時だけ反応
        if raw_distance < min_allowed_distance - boundary_deadzone:
            desired_lock_mode = "MIN"
            locked_distance = min_allowed_distance
        elif raw_distance > max_allowed_distance + boundary_deadzone:
            desired_lock_mode = "MAX"
            locked_distance = max_allowed_distance

    # ロックに新規突入した瞬間は、直前の自由姿勢を使って固定する。
    # ただし、横移動や回転っぽい操作までは止めないため、
    # 軸方向の前後移動と判定できる時だけ位置ロックを強く適用する。
    if desired_lock_mode:
        if (not props.lock_active) or (props.lock_mode != desired_lock_mode):
            props.lock_active = True
            props.lock_mode = desired_lock_mode
            props.lock_distance = float(locked_distance)
        else:
            props.lock_distance = float(locked_distance)

        rotation_delta_deg = _rotation_change_degrees(camera_obj, props)
        # 回転操作が見えるフレームでは、距離ロック位置へ即時で戻しすぎない。
        # ただしレンズ計算は常にロック距離を使い、ズームだけ進んでしまうことは防ぐ。
        if movement_is_dolly_like and rotation_delta_deg < 0.35:
            _restore_locked_pose(scene, camera_obj, target_obj, props.lock_distance)
        effective_distance = float(props.lock_distance)
    else:
        # ロック解除、通常状態へ復帰
        props.lock_active = False
        props.lock_mode = ""
        props.lock_distance = 0.0
        effective_distance = raw_distance
        _snapshot_free_pose(scene, camera_obj, target_obj)

    f = f0 * (effective_distance / d0)

    # 念のためレンズ側もクランプ
    if f < props.lens_min:
        f = props.lens_min
    if f > props.lens_max:
        f = props.lens_max

    try:
        if not getattr(camera_obj, "data", None):
            return
        if getattr(camera_obj.data, "type", "PERSP") != 'PERSP':
            return
        camera_obj.data.lens = float(f)
        props.last_auto_lens = float(f)
        cam_loc_after = camera_obj.matrix_world.translation.copy()
        props.last_eval_cam_loc = (float(cam_loc_after.x), float(cam_loc_after.y), float(cam_loc_after.z))
    except Exception:
        return


def _depsgraph_handler(scene, depsgraph):
    # シーンごとの更新で呼ばれる
    global _DZ_UPDATING

    if _DZ_UPDATING:
        return

    # プロパティ未登録の間は何もしない
    if not hasattr(scene, "mpm_dolly_props"):
        return

    props = scene.mpm_dolly_props
    if not props.enabled:
        return

    cam = scene.camera
    if cam is None or getattr(cam, "type", "") != "CAMERA" or not getattr(cam, "data", None):
        return
    if getattr(cam.data, "type", "PERSP") != 'PERSP':
        return

    tgt = props.target_obj
    if tgt is None or tgt.name not in bpy.data.objects:
        return

    # ドリーズーム有効中は焦点距離の手動入力を受け付けない。
    # もし別UI等から値が変わっても、次の自動更新で計算値へ戻す。

    _DZ_UPDATING = True
    try:
        _update_lens(scene, cam, tgt)
    finally:
        _DZ_UPDATING = False


def _ensure_handler_registered():
    # 二重登録防止
    handlers = bpy.app.handlers.depsgraph_update_post
    for h in handlers:
        if getattr(h, "__name__", "") == _depsgraph_handler.__name__:
            return
    handlers.append(_depsgraph_handler)


def _ensure_handler_unregistered():
    handlers = bpy.app.handlers.depsgraph_update_post
    remove_list = [h for h in handlers if getattr(h, "__name__", "") == _depsgraph_handler.__name__]
    for h in remove_list:
        try:
            handlers.remove(h)
        except Exception:
            pass


# -----------------------------------------------------------------------------
# プロパティ
# -----------------------------------------------------------------------------


def _on_enabled_update(self, context):
    # enabled をONにした瞬間、基準を取り直す
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    cam = scene.camera
    tgt = self.target_obj

    if self.enabled:
        _ensure_handler_registered()
        if cam is not None and getattr(cam, "type", "") == "CAMERA" and tgt is not None:
            _capture_baseline(scene, cam, tgt)
    else:
        # OFF：ハンドラは一旦解除（簡易）
        self.lock_active = False
        self.lock_mode = ""
        self.lock_distance = 0.0
        _ensure_handler_unregistered()


def _poll_target_obj(props, obj):
    # ターゲット選択候補の絞り込み（UIで文字列を指定した場合のみ）
    if obj is None:
        return False
    try:
        use_filter = bool(getattr(props, "use_name_filter", False))
        text = str(getattr(props, "name_filter_text", "") or "").strip()
    except Exception:
        return True
    if not use_filter:
        return True
    if text == "":
        return True
    return text in obj.name


class MPM_DollyProps(PropertyGroup):
    enabled: BoolProperty(
        name="有効",
        description="ON中、カメラ移動に合わせてレンズを自動調整してドリーズームにします",
        default=False,
        update=_on_enabled_update,
    )

    target_obj: PointerProperty(
        name="ターゲット",
        description="見かけサイズを固定したいターゲット（Empty等）",
        type=bpy.types.Object,
        poll=lambda self, obj: _poll_target_obj(self, obj),
    )

    use_name_filter: BoolProperty(
        name="候補フィルタ",
        description="ONのとき、ターゲット選択の候補を『含む文字』で絞り込みます",
        default=False,
    )

    name_filter_text: StringProperty(
        name="含む文字",
        description="この文字列を名前に含むオブジェクトだけを候補に表示します",
        default="",
        maxlen=256,
    )

    baseline_distance: FloatProperty(
        name="基準距離",
        description="ON時点のカメラ→ターゲット距離（内部用）",
        default=0.0,
        min=0.0,
    )

    baseline_lens: FloatProperty(
        name="基準レンズ",
        description="ON時点の焦点距離(mm)（内部用）",
        default=50.0,
        min=0.01,
    )


    last_eval_cam_loc: FloatVectorProperty(
        name="最終評価カメラ位置",
        description="内部用：前回評価時のカメラ位置",
        size=3,
        default=(0.0, 0.0, 0.0),
    )
    last_auto_lens: FloatProperty(
        name="直前自動レンズ",
        description="ドリーズームが最後に自動設定した焦点距離(mm)（内部用）",
        default=0.0,
        min=0.0,
    )

    lens_min: FloatProperty(
        name="レンズ最小(mm)",
        description="レンズの最小値（暴走防止）",
        default=18.0,
        min=0.1,
    )

    lens_max: FloatProperty(
        name="レンズ最大(mm)",
        description="レンズの最大値（暴走防止）",
        default=200.0,
        min=0.1,
    )

    min_distance: FloatProperty(
        name="最小距離",
        description="ターゲットに近づきすぎたときの破綻防止（距離クランプ）",
        default=0.05,
        min=0.0001,
    )

    lock_active: BoolProperty(
        name="ロック中",
        description="レンズ限界により距離ロック中（内部用）",
        default=False,
    )

    lock_mode: StringProperty(
        name="ロック種別",
        description="MIN / MAX（内部用）",
        default="",
        maxlen=8,
    )

    lock_distance: FloatProperty(
        name="ロック距離",
        description="ロック時に維持するカメラ-ターゲット距離（内部用）",
        default=0.0,
        min=0.0,
    )

    last_free_distance: FloatProperty(
        name="自由距離",
        description="ロック前に最後に安定していた距離（内部用）",
        default=0.0,
        min=0.0,
    )

    last_free_direction: FloatVectorProperty(
        name="自由方向",
        description="ロック前に最後に安定していた方向ベクトル（内部用）",
        size=3,
        default=(0.0, 0.0, 1.0),
        subtype='XYZ',
    )

    last_free_rot_mode: StringProperty(
        name="自由回転モード",
        description="ロック前の回転モード（内部用）",
        default="XYZ",
        maxlen=32,
    )

    last_free_rot_euler: FloatVectorProperty(
        name="自由オイラー",
        description="ロック前のオイラー回転（内部用）",
        size=3,
        default=(0.0, 0.0, 0.0),
        subtype='EULER',
    )

    last_free_rot_quat: FloatVectorProperty(
        name="自由クォータニオン",
        description="ロック前のクォータニオン（内部用）",
        size=4,
        default=(1.0, 0.0, 0.0, 0.0),
    )

    last_free_rot_axis_angle: FloatVectorProperty(
        name="自由軸角",
        description="ロック前の軸角回転（内部用）",
        size=4,
        default=(0.0, 0.0, 1.0, 0.0),
    )


_DOLLY_CLASSES = (
    MPM_DollyProps,
)


def get_dolly_props(scene):
    # UI側が安全に呼べるアクセサ
    try:
        return scene.mpm_dolly_props
    except Exception:
        return None


def register_dolly():
    for cls in _DOLLY_CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Scene.mpm_dolly_props = PointerProperty(type=MPM_DollyProps)

    # Blender 5.0 の有効化直後のRestrictedData対策：
    # ハンドラ自体は登録しておき、enabledがOFFなら即returnする。
    _ensure_handler_registered()


def unregister_dolly():
    _ensure_handler_unregistered()

    if hasattr(bpy.types.Scene, "mpm_dolly_props"):
        try:
            del bpy.types.Scene.mpm_dolly_props
        except Exception:
            pass

    for cls in reversed(_DOLLY_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


# -------------------------------
# ファイル名：dolly.py
# Version Footer: 1.113
# -------------------------------
