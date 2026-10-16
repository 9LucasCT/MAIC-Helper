# 平台接口与数据流程

这些信息来自学生端页面的调用逻辑，不是平台的公开接口承诺。平台升级后可能变化；不要把错误响应或页数不符当作导出成功。

## 课程与目录

登录后通过同源请求调用：

| 路径 | 方法 | 请求字段 | 用途 |
| --- | --- | --- | --- |
| `/api/course/curriculum` | POST | `user_id`, `current`, `pageSize` | 已加入课程；读取 `data.rows`、`data.total` |
| `/api/course/module/list` | POST | `user_id`, `course_id` | 课程目录；顶层是讲，`children` 是节 |

前端从浏览器本地存储读取用户 ID 和 token，请求携带 `Authorization: Bearer ...`。存在 `X-Daue` 时一同发送，并按响应更新。程序不把这些凭据写进输出或日志。

课堂路由为 `/classroom/{course_id}/{chapter_id}/{module_id}`；这里 `chapter_id` 对应讲，`module_id` 对应节。

## 课堂分页

课堂连接 `/api/student/v8/conv/ws/inclass`。程序监听接收到的消息，不自行发送翻页、答题或完成指令。

- `EVENT_RES_INIT_DATA`：`data.rows` 是当前课堂返回的分页列表。
- `EVENT_MESSAGE_SCRIPT` 等事件：可能追加或更新页面；按 `page_id` 去重。
- `content.slide.url`：单页课件 PDF 地址。
- `content.script.script`：原始讲稿文本。
- `content.script.segments[].text`：分段文本；存在时，页面显示文本优先由它们拼接。

文本导出还会读取“分页讲稿”面板中 `.markdown-wrapper` 的渲染文本，并由相邻 `[data-page]` 标签对应课堂页码。问答等非幻灯片内容会跳过，保留原页码。

## 完整性检查

目录的 `total` 与课堂返回的页面数不符时，本节失败；不会为补齐讲稿自动推进学习。PDF 下载地址只接受已知 MAIC HTTPS 文件域名，校验文件头、页数，并在合并成功后替换最终文件。

正常打开课堂依然会触发网站自身的会话、访问和心跳请求。不要把“程序不主动发送学习指令”理解为“访问课堂不产生记录”。
