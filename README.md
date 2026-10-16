# MAIC-Helper

从 [MAIC 平台](https://www.maic.tsinghua.edu.cn/)导出课程资料的 Python 命令行工具。登录自己的账号后，可以选择已加入的课程和章节，提取分页讲稿为 TXT，并下载、合并每节课的 PDF 课件。

输出按「课程 → 讲 → 节文件」整理，只保留每节的 `.pdf` 和 `.txt`，适合本地阅读、检索和复习。本工具不是 MAIC 官方客户端。

## 功能

- 交互式选课，或通过命令参数指定课程、章节。
- 讲稿、PDF 可分别导出，也可同时导出。
- 讲目录和节文件自动加序号，按平台目录排序。
- 使用进度条展示章节和 PDF 页数进度。
- 重复导出时，成功生成的新文件替换对应旧文件；失败不会覆盖已有成功结果。

## 安装

需要 **Python 3.10 或更高版本**，以及 **Microsoft Edge 或 Google Chrome**。目前主要在 Windows 下验证。

```powershell
git clone https://github.com/9LucasCT/MAIC-Helper.git
cd MAIC-Helper
python -m pip install -r requirements.txt
```

使用你已有的 Python 或 Conda 环境即可。程序不自动创建虚拟环境，也不自动安装依赖。

如果没有 Edge/Chrome，可以自行安装 Playwright Chromium：

```powershell
python -m playwright install chromium
```

## 快速开始

```powershell
python maic.py
```

1. 程序打开独立浏览器，在其中正常登录 MAIC。
2. 回到终端，输入课程序号。
3. 输入需要导出的章节序号，或直接回车选择全部已学完且未锁定的章节。
4. 选择 `text`（讲稿）、`pdf`（课件）或 `both`（两者，默认）。

完成后，终端会显示保存位置及成功、失败节数。默认输出到**当前工作目录**下的 `exports/`。

首次使用需要在程序打开的浏览器里登录；它不会直接复用日常浏览器的账号。后续运行会复用本工具自己的登录状态。运行时请不要手动切换它打开的课堂页面。按 **Ctrl+C** 可以中止。

## 命令行用法

### 查看课程和章节

```powershell
# 列出已加入课程及课程 ID
python maic.py courses

# 查看某门课程的章节序号
python maic.py chapters --course "课程名称"
```

`--course` 可以填写完整课程名、课程 ID，或能唯一匹配课程的名称片段。如果匹配到多门课程，程序会提示使用更完整的名称或 ID。

### 导出资料

```powershell
# 导出默认选中的已学完章节，同时保存讲稿和 PDF
python maic.py export --course "课程名称"

# 仅提取讲稿
python maic.py export --course "课程名称" --type text

# 仅下载合并 PDF，选择第 1、3、4、5 节
python maic.py export --course "课程名称" --chapters 1,3-5 --type pdf

# 指定输出目录
python maic.py export --course "课程名称" --type both --output "D:\MAIC-exports"
```

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `--course` | 课程名、唯一名称片段或课程 ID | `export` 和 `chapters` 命令必填 |
| `--chapters` | `1,3-5` 等章节序号，或 `all` | 全部已学完且未锁定的章节 |
| `--type` | `text`、`pdf`、`both` | `both` |
| `--output` | 输出目录 | 当前工作目录下的 `exports` |

`--chapters` 使用 `chapters` 命令列出的**整门课程连续序号**，不是文件名里的「讲-节」编号。`all` 表示选择所有未锁定章节，但不保证未学完章节已经提供完整资料。

查看完整帮助：

```powershell
python maic.py --help
python maic.py export --help
```

## 输出结构

```text
exports/
└─ 课程名称/
   ├─ 01-第1讲 课程概述/
   │  ├─ 01-01-第一节：引言.pdf
   │  ├─ 01-01-第一节：引言.txt
   │  ├─ 01-02-第二节：基本概念.pdf
   │  └─ 01-02-第二节：基本概念.txt
   └─ 02-第2讲 基础知识/
      ├─ 02-01-第一节：基础模型.pdf
      └─ 02-01-第一节：基础模型.txt
```

- 序号来自整门课程的平台目录顺序，不依赖老师是否在标题中写明「第几讲／节」。至少两位补零，超过 99 时扩展位数；只选部分章节也保留原序号。
- 每个 PDF 合并该节的幻灯片页面；TXT 按 `【P1】`、`【P2】` 等标签分隔讲稿。问答等非幻灯片内容跳过，保留原课堂页码。
- 不生成时间戳目录、整课汇总、JSON 讲稿或导出报告。单页 PDF 仅作为临时文件使用，处理结束后清理。
- 再次运行使用相同的目标路径，只更新本次选择且成功导出的文件。未选择的资料保留；失败时请查看终端提示，已有旧文件可能仍是上次的版本。
- Windows 文件名不支持的字符会替换为下划线。旧版导出目录不会自动迁移或删除。

## 登录、完整性和使用范围

本工具通过正常登录后的课堂页面获取资料，不需要手动复制密码或令牌。登录状态保存在程序目录下的 `.session/`；**不要分享或提交该目录**。关闭程序和它打开的浏览器后，删除 `.session/` 可清除本工具的登录状态。

程序只处理当前账号有权访问、且课堂已经提供的内容，不自动翻完未学章节，不提交答案或主动发送完成指令。如果目录页数与返回内容不符，或者讲稿缺页，会将该节标记为失败并在终端说明。正常打开课堂仍会产生平台自己的访问、会话和心跳请求。

课件以平台实际提供的 PDF 保存，不恢复原始 PPT/PPTX。平台接口或页面结构变化可能影响使用；遇到问题时，请保留不含账号凭据的报错信息。

终端退出码：全部成功为 `0`，导出失败为 `1`，缺少依赖为 `2`，中断为 `130`；命令参数错误也返回非零。

## 常见问题

**提示缺少依赖**

用运行程序的同一个 Python 安装依赖，避免 `pip` 和 `python` 属于不同环境：

```powershell
python -m pip install -r requirements.txt
```

**浏览器无法启动**

确认 Edge/Chrome 已安装，且本工具之前打开的浏览器已关闭。也可以执行前面的 Chromium 安装命令。

**未学完章节导出失败**

课堂可能只提供已学习页面。先在平台正常学习，再重新导出；程序不会为了补齐资料自动推进课程。

**只想更新某几节**

使用 `--chapters` 选择对应章节，程序不会重新导出未选中的节。

## 开发与测试

```text
maic.py                 命令行入口、课堂数据接收与导出逻辑
requirements.txt        运行依赖
docs/protocol.md        平台接口与数据流程笔记
tests/test_maic.py      自动检查
tests/integration_check.py  本机模拟课堂流程检查
tests/support.py        自行生成测试用 PDF
```

```powershell
# 自动检查，不访问真实课程
python -m unittest discover -s tests -v

# 本机模拟课堂：需要已有 Edge/Chrome 或 Playwright Chromium
python -m tests.integration_check
```

测试使用自行生成的 PDF 和讲稿，不依赖账号、下载资料或前期调研缓存。Git 忽略本地 `.session/`、`exports/`、虚拟环境及缓存。

本工具使用 [Playwright](https://playwright.dev/python/) 读取课堂接收到的数据，使用 [pypdf](https://pypdf.readthedocs.io/) 合并 PDF，并使用 [tqdm](https://tqdm.github.io/) 显示进度。
