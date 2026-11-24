# wow-infinite-register（通用可移植包）

在真实 Windows 桌面上自动创建《魔兽世界："无限"》角色，遇"阵营人口不平衡"等拒绝弹窗按节奏重试，直到建号成功。

> 合规提示：自动化模拟键鼠可能违反游戏用户协议，存在限流/短期封禁风险。仅用于个人研究与本机辅助，风险自负；启动前须向用户明示并取得确认。

## 目录结构

```
wow-infinite-register/
├── SKILL.md                      # 技能规范、安全闸门、工作流、排障速查
├── README.md                     # 本文件
├── references/
│   └── calibration.md            # 校准指南 / detect 类型 / 阈值技巧
└── scripts/
    ├── register.py                # 主程序：validate / probe / trial / infinite
    ├── desktop_ctl.py             # Win32 真实桌面控制（零第三方依赖）
    ├── vision.py                   # 连通域图像识别（红/金/绿区域）
    ├── calibrate.py              # 交互式采集 capture / 框选 boxes / 校验 probe
    ├── config.example.json         # 可移植示例配置（坐标/路径为样例，须本机重采）
    └── presets/
        └── pvp-male-skydancer-druid.json   # 字段结构样例（勿直接运行）
```

## 运行前提

- Windows，Python 3.8+，**无第三方依赖**（仅用标准库 + ctypes）。
- 游戏与战网已安装并可正常登录；屏幕缩放/DPI 在采集与运行期间保持一致。
- 游戏窗口标题默认取「魔兽世界」；若不同，改运行配置 `meta.window_title`。

## 快速上手

1. **复制示例配置**：把 `scripts/config.example.json` 复制为 `scripts/config_run.json`。
2. **填路径**：在 `config_run.json` 填好本机 `client_exe`（Wow.exe）、`bnet_exe`（Battle.net.exe）的绝对路径。
3. **现场校准坐标**（换机/换分辨率必须重做）：
   ```bash
   cd scripts
   python calibrate.py capture --out config_run.json --ruleset PVP --race 兽人 --klass 猎人 --gender 女 --main 雨读秋荷 --sub 青兰
   python calibrate.py boxes   --config config_run.json
   python register.py --config config_run.json --mode probe
   ```
   按提示点击/框选各界面元素；probe 中性别/种族/职业三项应在选中态全 True、非目标态 False。
4. **校验**：
   ```bash
   python register.py --config config_run.json --mode validate
   ```
5. **试跑 3 轮**（须先向用户交代并获明确确认）：
   ```bash
   python -u register.py --config config_run.json --mode trial --rounds 3 --tag trial
   ```
6. **无限模式**（试跑信号稳定、二次确认后）：
   ```bash
   python -u register.py --config config_run.json --mode infinite --tag inf
   ```

## 恢复模式（`meta.recovery_mode`）

- `restart_client`（推荐，社区实测）：弹窗确定后 ALT+F4 关游戏、战网保留，回战网点「进入游戏」（`meta.bnet_enter`）重开客户端。
- `in_client`：客户端内按后退链退回登录页。

## 重要说明

- 本包**不内置可用坐标**：`config.example.json` / `presets` 里的数字只是字段结构与阈值量级参考，绑定特定显示器与分辨率，不可直接照搬运行。
- 坐标必须在目标机上用 `calibrate.py` + `vision.py` 现场采集，并做选中/未选双态复核。
- 服务端阵营锁是官方动态平衡，自动化只负责按节奏蹲名额，**不能绕过服务端限制**。
- 限流（throttled）弹窗尚无专用建模，当前由未知屏 `unknown_backoff`（默认 180 秒）退避兜底。

## 命令行速查

| 命令 | 作用 |
| --- | --- |
| `python register.py --config <cfg> --mode validate` | 校验配置合法，不碰桌面 |
| `python register.py --config <cfg> --mode probe` | 打印各检测项 True/False 与数值 |
| `python register.py --config <cfg> --mode trial --rounds 3` | 固定轮数试跑，存截图与 jsonl |
| `python register.py --config <cfg> --mode infinite` | 无限重试，成功写 `loop_success.png` 后停止 |
| `python desktop_ctl.py shot <png>` | 抓全屏截图 |
| `python desktop_ctl.py info` | 屏幕尺寸 + 前台窗口 + 顶层窗口列表 |
