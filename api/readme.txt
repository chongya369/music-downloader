api/ 目录说明
================================================

本目录用于存放各音乐平台所需的预编译 API 服务二进制。
这些二进制体积较大，不随源码仓库分发包送，需自行从官方 GitHub Release 下载后放置。
程序启动时（或首次业务请求时）会自动按当前操作系统选择并拉起对应的二进制。

不同平台需要放置的二进制文件如下：请按列表中的"文件名"放入本目录，
注意 Windows 与 Linux 的文件名不同（Windows 带 .exe 后缀，Linux 无后缀）。

------------------------------------------------
一、网易云音乐（NeteaseCloudMusicApi-enhanced）
------------------------------------------------
  对应 GitHub 项目：
      https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced

  Windows x64:   ncm-api-win-x64.exe
  Linux   x64:   ncm-api-linux-x64

  从上述项目的 Releases 页面下载对应文件后放入本目录。

------------------------------------------------
二、QQ 音乐（qqmusic-api）
------------------------------------------------
  对应 GitHub 项目：
      https://github.com/chongya369/qqmusic-api-py

  Windows x64:   qqmusic-api-win-x64.exe
  Linux   x64:   qqmusic-api-linux-x64

  从上述项目的 Releases 页面下载对应文件后放入本目录。

------------------------------------------------
三、酷狗音乐（KuGouMusicApi）
------------------------------------------------
  对应 GitHub 项目：
      https://github.com/Lines98/KuGouMusicApi

  Windows x64:   kugou_api_win.exe
  Linux   x64:   kugou_api_linux

  从上述项目的 Releases 页面下载对应文件后放入本目录，
  也可使用项目内置预编译包（已与 v1.5.1 兼容）。

------------------------------------------------
注意
------------------------------------------------
1. 请保证放入的文件名与上表完全一致（含大小写与后缀），否则程序无法识别。
2. 二进制仅需放置当前使用平台的单个文件即可，也可同时放置多平台文件以便跨平台迁移。
3. 若对应二进制缺失，相关功能不可用，但不会影响程序其余流程。