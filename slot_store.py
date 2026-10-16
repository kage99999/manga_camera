# -*- coding: utf-8 -*-
# Manga Camera 1.210: persistent camera-stock libraries, independent of .blend.

import copy
import json
import os
import re
import shutil
import unicodedata
import uuid
import zipfile

import bpy

from .storage import (_atomic_writer, _normalize_dirpath, _normalize_saved_list,
                      _read_stock_json, _write_json_atomic)


CATALOG = '_manga_camera_slots.json'
SETTINGS = 'manga_camera_slots_settings.json'
FORMAT = 'manga_camera_slots'
MAX_ZIP_BYTES = 512 * 1024 * 1024


def _same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _stamp(path):
    stat = os.stat(path)
    return stat.st_mtime_ns, stat.st_size


def _name(value):
    name = unicodedata.normalize('NFC', str(value)).strip()
    if (not name or len(name) > 80 or name.startswith('.') or name.endswith('.')
            or re.search(r'[\x00-\x1f<>:"/\\|?*]', name)
            or name.split('.')[0].upper() in {
                'CON', 'PRN', 'AUX', 'NUL',
                *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
            or (name + '.json').casefold() == CATALOG.casefold()):
        raise ValueError('スロット名に使用できない文字、長すぎる名前、または空の名前です')
    return name


def _payload(data):
    if (not isinstance(data, dict) or 'all_object_data' in data
            or not isinstance(data.get('camera_data'), list)
            or any(not isinstance(item, dict) for item in data['camera_data'])):
        raise ValueError('通常ストック用のJSONではありません')
    home = os.path.expanduser('~')
    return {
        'camera_data': _normalize_saved_list(data['camera_data']),
        'output_folder_path': _normalize_dirpath(data.get('output_folder_path') or home),
        'background_image_folder_path': _normalize_dirpath(data.get('background_image_folder_path') or home),
    }


def _catalog(entries):
    return {'format': FORMAT, 'version': 1, 'revision': uuid.uuid4().hex, 'slots': entries}


def _validate_catalog(data):
    if (not isinstance(data, dict) or data.get('format') != FORMAT
            or data.get('version') != 1 or not isinstance(data.get('slots'), list)
            or not isinstance(data.get('revision'), str)):
        raise ValueError('スロット目録の形式が正しくありません')
    ids, names, files = set(), set(), set()
    for entry in data['slots']:
        if not isinstance(entry, dict):
            raise ValueError('スロット目録の項目が正しくありません')
        ident, name, filename = entry.get('id'), entry.get('name'), entry.get('file')
        if (not isinstance(ident, str) or not re.fullmatch(r'[0-9a-f]{32}', ident)
                or not isinstance(name, str) or _name(name) != name
                or filename != name + '.json'
                or ident in ids or name.casefold() in names or filename.casefold() in files):
            raise ValueError('スロット目録に重複名または不正なパスがあります')
        ids.add(ident)
        names.add(name.casefold())
        files.add(filename.casefold())
    return data


def _read_catalog(folder):
    with open(os.path.join(folder, CATALOG), encoding='utf-8-sig') as handle:
        return _validate_catalog(json.load(handle))


def _new_json(path, data):
    # Reserve the name exclusively. Only a completed file is published in the catalog.
    with open(path, 'x', encoding='utf-8'):
        pass
    try:
        _write_json_atomic(path, data)
    except Exception:
        os.unlink(path)
        raise


class SlotLibrary:
    def __init__(self, manager, config_dir=None):
        self.manager = manager
        self.config_dir = config_dir or bpy.utils.user_resource('CONFIG')
        self.settings_path = os.path.join(self.config_dir, SETTINGS)
        self.folder = ''
        self.catalog = None
        self.active_id = ''
        self.file_stamp = None
        self.error = ''
        self.enum_items = [('NONE', '(スロットなし)', '', 0)]

    @property
    def entries(self):
        return self.catalog['slots'] if self.catalog else []

    @property
    def active(self):
        return next((entry for entry in self.entries if entry['id'] == self.active_id), None)

    def _path(self, entry, folder=None):
        root = os.path.realpath(folder or self.folder)
        path = os.path.join(root, entry['file'])
        if os.path.dirname(os.path.realpath(path)) != root or os.path.islink(path):
            raise ValueError('保存先の外部を指すスロットは操作できません')
        return path

    def _remember(self, folder, ident):
        _write_json_atomic(self.settings_path, {
            'version': 1, 'folder': folder, 'active_slot': ident})

    def _rebuild_enum(self):
        self.enum_items = [(e['id'], e['name'], e['file'], i + 1)
                           for i, e in enumerate(self.entries)] or [('NONE', '(スロットなし)', '', 0)]

    def _read(self, entry, folder=None):
        path = self._path(entry, folder)
        before = _stamp(path)
        data = _payload(_read_stock_json(path, 'camera_data'))
        after = _stamp(path)
        if before != after:
            raise ValueError('読込中にスロットが変更されました。もう一度選択してください')
        return data, after

    def _adopt(self, folder, catalog, ident, data, stamp=None):
        self.folder, self.catalog, self.active_id = folder, catalog, ident
        self.file_stamp, self.error = stamp, ''
        manager = self.manager
        manager.saved_camera_data = data['camera_data']
        manager._normalized_saved_data = manager.saved_camera_data
        manager.output_folder_path = data['output_folder_path']
        manager.background_image_folder_path = data['background_image_folder_path']
        manager.save_file_path = self._path(self.active) if self.active else ''
        manager._blocked_save_path = None
        self._rebuild_enum()

    def snapshot(self):
        manager = self.manager
        return _payload({'camera_data': manager.saved_camera_data,
                         'output_folder_path': manager.output_folder_path,
                         'background_image_folder_path': manager.background_image_folder_path})

    def _check_catalog(self):
        if self.catalog is None:
            raise ValueError('設定からスロット保存先を指定してください')
        disk = _read_catalog(self.folder)
        if disk != self.catalog:
            raise ValueError('別の操作でスロット目録が変更されました。保存先を再指定してください')

    def check_write(self):
        self._check_catalog()
        if not self.active:
            raise ValueError('先にスロットを作成してください')
        if self.error or self.file_stamp != _stamp(self._path(self.active)):
            raise ValueError('スロットが変更されたか読込に失敗しています。スロットを選び直してください')

    def did_save(self):
        self.file_stamp = _stamp(self._path(self.active))

    def start(self):
        try:
            with open(self.settings_path, encoding='utf-8-sig') as handle:
                settings = json.load(handle)
        except FileNotFoundError:
            settings = None
        if settings is not None:
            if (not isinstance(settings, dict) or settings.get('version') != 1
                    or not isinstance(settings.get('folder'), str)
                    or not os.path.isabs(settings['folder'])
                    or not isinstance(settings.get('active_slot'), str)):
                raise ValueError('スロット保存先の設定が壊れています。設定から保存先を指定してください')
            self.folder = settings['folder']
            self.catalog = _read_catalog(self.folder)
            self._rebuild_enum()
            self.select(settings['active_slot'], fallback=True)
            return

        self.folder = os.path.join(self.config_dir, 'manga_camera_slots')
        if os.path.isfile(os.path.join(self.folder, CATALOG)):
            self.catalog = _read_catalog(self.folder)
            self.select('', fallback=True)
            return
        legacy = self.manager.save_file_path
        data = (_payload(_read_stock_json(legacy, 'camera_data'))
                if os.path.exists(legacy) else self.snapshot())
        entry = {'id': uuid.uuid4().hex, 'name': '既存データ', 'file': '既存データ.json'}
        self._install_folder(self.folder, _catalog([entry]), [data], entry['id'])

    def select(self, ident, *, fallback=False):
        catalog = _read_catalog(self.folder)
        entries = catalog['slots']
        entry = next((e for e in entries if e['id'] == ident), None)
        if entry is None and entries:
            if not fallback:
                raise ValueError('選択したスロットが見つかりません')
            entry = entries[0]
        ident = entry['id'] if entry else ''
        data, stamp = self._read(entry) if entry else (_payload({'camera_data': []}), None)
        self._remember(self.folder, ident)
        self._adopt(self.folder, catalog, ident, data, stamp)

    def reload(self):
        if not self.active:
            raise ValueError('読み込むスロットがありません')
        self.select(self.active_id)

    def _install_folder(self, folder, catalog, payloads, active_id):
        os.makedirs(folder, exist_ok=True)
        if os.path.lexists(os.path.join(folder, CATALOG)):
            raise ValueError('保存先には既にスロット目録があります')
        created = []
        try:
            for entry, data in zip(catalog['slots'], payloads):
                path = self._path(entry, folder)
                _new_json(path, data)
                created.append(path)
            path = os.path.join(folder, CATALOG)
            _new_json(path, catalog)
            created.append(path)
            entry = next((e for e in catalog['slots'] if e['id'] == active_id), None)
            data, stamp = self._read(entry, folder) if entry else (_payload({'camera_data': []}), None)
            self._remember(folder, active_id)
        except Exception:
            for path in reversed(created):
                os.unlink(path)
            raise
        self._adopt(folder, catalog, active_id, data, stamp)

    def set_folder(self, folder):
        folder = os.path.realpath(_normalize_dirpath(folder))
        if not os.path.isdir(folder):
            raise ValueError('存在するフォルダを指定してください')
        if os.path.isfile(os.path.join(folder, CATALOG)):
            catalog = _read_catalog(folder)
            entry = next((e for e in catalog['slots'] if e['id'] == self.active_id), None)
            if entry is None and catalog['slots']:
                entry = catalog['slots'][0]
            ident = entry['id'] if entry else ''
            data, stamp = self._read(entry, folder) if entry else (_payload({'camera_data': []}), None)
            self._remember(folder, ident)
            self._adopt(folder, catalog, ident, data, stamp)
        else:
            if self.catalog is not None:
                self._check_catalog()
                payloads = [self._read(entry)[0] for entry in self.entries]
                catalog = _catalog(copy.deepcopy(self.entries))
                active_id = self.active_id or (self.entries[0]['id'] if self.entries else '')
            else:
                # Recovery from a missing library must not silently replace it with an empty copy.
                if self.error:
                    raise ValueError('元の保存先を復旧するか、既存のスロット目録があるフォルダを指定してください')
                entry = {'id': uuid.uuid4().hex, 'name': '既存データ', 'file': '既存データ.json'}
                catalog, payloads, active_id = _catalog([entry]), [self.snapshot()], entry['id']
            self._install_folder(folder, catalog, payloads, active_id)

    def _publish(self, entries, ident, data, stamp):
        self._check_catalog()
        previous = self.catalog
        updated = _catalog(entries)
        path = os.path.join(self.folder, CATALOG)
        _write_json_atomic(path, updated)
        try:
            self._remember(self.folder, ident)
        except Exception:
            _write_json_atomic(path, previous)
            raise
        self._adopt(self.folder, updated, ident, data, stamp)

    def _unique_name(self, name, names):
        base = _name(name)
        result, suffix = base, 2
        while result.casefold() in names or os.path.lexists(os.path.join(self.folder, result + '.json')):
            result = _name(f'{base[:65]} ({suffix})')
            suffix += 1
        return result

    def add(self, items, *, unique=False, active_index=0):
        self._check_catalog()
        entries = copy.deepcopy(self.entries)
        names = {e['name'].casefold() for e in entries}
        prepared = []
        for name, data in items:
            name = self._unique_name(name, names) if unique else _name(name)
            if name.casefold() in names:
                raise ValueError('同名のスロットが既にあります')
            names.add(name.casefold())
            entry = {'id': uuid.uuid4().hex, 'name': name, 'file': name + '.json'}
            prepared.append((entry, _payload(data)))
        if not prepared:
            raise ValueError('読み込むスロットがありません')
        created = []
        try:
            for entry, data in prepared:
                path = self._path(entry)
                _new_json(path, data)
                created.append(path)
                entries.append(entry)
            selected, data = prepared[active_index]
            self._publish(entries, selected['id'], data, _stamp(self._path(selected)))
        except Exception:
            # If even the catalog rollback failed, retain files it still references.
            # Keep extra files rather than risk losing referenced data.
            try:
                referenced = {e['file'] for e in _read_catalog(self.folder)['slots']}
            except Exception:
                referenced = {os.path.basename(path) for path in created}
            for path in reversed(created):
                if os.path.basename(path) not in referenced:
                    os.unlink(path)
            raise

    def create(self, name):
        data = {'camera_data': [], 'output_folder_path': self.manager.output_folder_path,
                'background_image_folder_path': self.manager.background_image_folder_path}
        self.add([(name, data)])

    def rename(self, ident, name):
        self.check_write()
        if ident != self.active_id:
            raise ValueError('名前の入力中に選択スロットが変わりました。やり直してください')
        name = _name(name)
        if name == self.active['name']:
            return False
        if any(e['id'] != ident and e['name'].casefold() == name.casefold() for e in self.entries):
            raise ValueError('同名のスロットが既にあります')
        entries = copy.deepcopy(self.entries)
        entry = next(e for e in entries if e['id'] == ident)
        entry.update(name=name, file=name + '.json')
        old_path, new_path = self._path(self.active), self._path(entry)
        case_only = _same_path(old_path, new_path)
        if not case_only and os.path.lexists(new_path):
            raise ValueError('変更先と同名のJSONがあります。別の名前を指定してください')
        updated = _catalog(entries)
        created = False
        try:
            if case_only:
                # Windows allows a spelling-only rename of the same file.
                os.rename(old_path, new_path)
            else:
                with open(new_path, 'xb'):
                    pass
                created = True
                # Preserve the JSON bytes, including unknown future-compatible fields.
                with open(old_path, 'rb') as source, _atomic_writer(new_path) as target:
                    shutil.copyfileobj(source, target)
            self.check_write()
            stamp = _stamp(new_path)
            _write_json_atomic(os.path.join(self.folder, CATALOG), updated)
        except Exception:
            if case_only:
                os.rename(new_path, old_path)
            elif created:
                os.unlink(new_path)
            raise
        # Stable slot IDs keep the remembered choice valid without changing settings.
        self.catalog, self.file_stamp = updated, stamp
        self.manager.save_file_path = new_path
        self._rebuild_enum()
        if not case_only:
            try:
                os.unlink(old_path)
            except OSError as exc:
                print(f'スロット名を変更しました。旧名JSONの削除は保留しました: {exc}')
        return True

    def delete(self, ident):
        self._check_catalog()
        if not self.active or ident != self.active_id:
            raise ValueError('確認中に選択スロットが変わりました。削除をやり直してください')
        self.check_write()
        remaining = [copy.deepcopy(e) for e in self.entries if e['id'] != ident]
        entry = remaining[0] if remaining else None
        data, stamp = self._read(entry) if entry else (_payload({'camera_data': []}), None)
        old_path = self._path(self.active)
        trash = os.path.join(self.folder, '.trash')
        os.makedirs(trash, exist_ok=True)
        if os.path.islink(trash) or os.path.dirname(os.path.realpath(trash)) != os.path.realpath(self.folder):
            raise ValueError('退避先が保存フォルダの外部を指しています')
        backup = os.path.join(trash, uuid.uuid4().hex + '_' + self.active['file'])
        _new_json(backup, self._read(self.active)[0])
        self._publish(remaining, entry['id'] if entry else '', data, stamp)
        try:
            os.unlink(old_path)
        except OSError as exc:
            # Catalog deletion has committed; retain the extra copy rather than lose data.
            print(f'スロット削除済み。元JSONの削除は保留しました: {exc}')

    def guard_export(self, path):
        if _same_path(path, self.settings_path):
            raise ValueError('スロット設定ファイルへ書き出せません')
        if self.folder and _same_path(os.path.dirname(path), self.folder):
            raise ValueError('バックアップはスロット保存先とは別のフォルダへ書き出してください')

    def export_json(self, path, data=None):
        self.check_write()
        self.guard_export(path)
        _write_json_atomic(path, self.snapshot() if data is None else data)

    def import_json(self, path):
        data = _payload(_read_stock_json(path, 'camera_data'))
        self.add([(os.path.splitext(os.path.basename(path))[0], data)], unique=True)

    def export_all(self, path):
        self._check_catalog()
        self.guard_export(path)
        payloads = [self._read(entry)[0] for entry in self.entries]
        catalog = copy.deepcopy(self.catalog)
        catalog['active_slot'] = self.active_id
        with _atomic_writer(path) as handle:
            with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(CATALOG, json.dumps(catalog, ensure_ascii=False))
                for entry, data in zip(self.entries, payloads):
                    archive.writestr(entry['file'], json.dumps(data, ensure_ascii=False))

    def import_all(self, path):
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if (len(names) != len(set(names)) or len(names) > 10001
                    or sum(i.file_size for i in infos) > MAX_ZIP_BYTES
                    or any(i.flag_bits & 1 for i in infos) or CATALOG not in names):
                raise ValueError('全スロットZIPの形式またはサイズが不正です')
            catalog = _validate_catalog(json.loads(archive.read(CATALOG).decode('utf-8-sig')))
            if set(names) != {CATALOG, *(e['file'] for e in catalog['slots'])}:
                raise ValueError('全スロットZIPに不正なパスまたは余分な項目があります')
            items = [(e['name'], _payload(json.loads(archive.read(e['file']).decode('utf-8-sig'))))
                     for e in catalog['slots']]
        active_index = next((i for i, e in enumerate(catalog['slots'])
                             if e['id'] == catalog.get('active_slot')), 0)
        self.add(items, unique=True, active_index=active_index)


def initialize(manager):
    store = SlotLibrary(manager)
    manager.slot_library = store
    try:
        store.start()
    except Exception as exc:
        store.error = str(exc)
        print(f'スロットを読み込めませんでした。元データは変更していません: {exc}')
    return store


# Version Footer: 1.210
