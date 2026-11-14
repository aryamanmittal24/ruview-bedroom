# Custom code compared with upstream

Reference: [`dd02efe`](https://github.com/ruvnet/RuView/commit/dd02efe). Counts compare the published custom files with that exact upstream base; they are file-line changes, not hours worked or a performance metric. Upstream files retained without changes are not counted here. Packaging exclusions (workflows, binaries, private runtime files) are separate from implementation work.

| File | Change | Lines |
| --- | --- | --- |
| [`scripts/c6-presence-watcher.py`](../scripts/c6-presence-watcher.py) | Modified | +102 / −6 |
| [`scripts/ruview-sensing-server.py`](../scripts/ruview-sensing-server.py) | Modified | +251 / −16 |
| [`ui/app.js`](../ui/app.js) | Modified | +13 / −67 |
| [`ui/components/DashboardTab.js`](../ui/components/DashboardTab.js) | Modified | +36 / −3 |
| [`ui/components/TabManager.js`](../ui/components/TabManager.js) | Modified | +0 / −0 |
| [`ui/index.html`](../ui/index.html) | Modified | +51 / −14 |
| [`ui/services/sensing.service.js`](../ui/services/sensing.service.js) | Modified | +28 / −0 |
| [`ui/style.css`](../ui/style.css) | Modified | +86 / −0 |
| [`ui/sw.js`](../ui/sw.js) | Modified | +11 / −5 |
| [`ui/utils/command-palette.js`](../ui/utils/command-palette.js) | Modified | +2 / −24 |
| [`ui/utils/data-source-banner.js`](../ui/utils/data-source-banner.js) | Modified | +1 / −1 |
| [`ui/utils/keyboard-shortcuts.js`](../ui/utils/keyboard-shortcuts.js) | Modified | +2 / −8 |
| [`ui/utils/router.js`](../ui/utils/router.js) | Modified | +1 / −1 |
| [`scripts/movement_lab.py`](../scripts/movement_lab.py) | Added | +175 / −0 |
| [`scripts/movement_learning.py`](../scripts/movement_learning.py) | Added | +267 / −0 |
| [`scripts/tests/test_movement_learning.py`](../scripts/tests/test_movement_learning.py) | Added | +89 / −0 |
| [`scripts/wiz-spacesense-watcher.py`](../scripts/wiz-spacesense-watcher.py) | Added | +151 / −0 |
| [`ui/components/BedroomScene.js`](../ui/components/BedroomScene.js) | Added | +128 / −0 |
| [`ui/components/GestureController.js`](../ui/components/GestureController.js) | Added | +184 / −0 |
| [`ui/components/RoomMap.js`](../ui/components/RoomMap.js) | Added | +224 / −0 |
| [`ui/movement.css`](../ui/movement.css) | Added | +8 / −0 |
| [`ui/movement.html`](../ui/movement.html) | Added | +35 / −0 |
| [`ui/movement.js`](../ui/movement.js) | Added | +174 / −0 |

To reproduce an exact diff, clone the upstream source at the revision above, then compare these files against this repository. This preserves attribution without importing upstream authors' commits as the owner's work.

[Actual publication commits and diffs](https://github.com/aryamanmittal24/ruview-bedroom/commits/main/)
