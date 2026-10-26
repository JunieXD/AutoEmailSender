# 网页样本

2026-10-25 手动获取，只用于离线解析回归。各文件开头注明来源；保留实际卡片、链接和文字，删除无关脚本与样式。挑战页和动态名单保留识别所需的脚本，测试不执行它们。

| 文件 | 来源 | 验证场景 |
|---|---|---|
| `muc.html` | https://xingong.muc.edu.cn/szdw/xyjs.htm | 中央民族大学卡片、图片链接、`onclick` 导航 |
| `mcgill.html` | https://www.cs.mcgill.ca/people/faculty/ | 麦吉尔大学名单，每位导师的姓名与邮箱归属 |
| `fudan.html` | https://software.fudan.edu.cn/zzjs/list.htm | 复旦大学未加载的动态教师列表 |
| `bupt.html` | https://scs.bupt.edu.cn/szjs1/jsyl.htm | 北京邮电大学 HTTP 412 挑战页 |
| `fzu.html` | https://chem.fzu.edu.cn/szdw/jsml1.htm | 福州大学图片链接中的 `mailto:` 联系方式 |
| `nju.html` | https://cs.nju.edu.cn/zhouzh/ | 南京大学真实个人页、中英文文字 |
| `nju-gbk.html` | 同上 | 从原 UTF-8 页面转码为 GBK，并修改编码声明，验证 GB2312／GBK 解码；不是网站原生编码的记录 |
| `seu.html` | https://cyber.seu.edu.cn/yjsds_22371/list.htm | 东南大学网址翻页和不可用的上一页控件 |
| `toronto.html` | https://www.cs.toronto.edu/~hinton/ | 多伦多大学真实个人页中的 `[at]`、`[dot]` 邮箱 |

Cloudflare 属性及错误输入等组合场景在测试中构造，不冒充真实网站样本。

`mcgill-emails.tsv` 是从同页逐项核对的 92 组姓名和邮箱，作为归属测试的期望数据，包含学校主任与全部教师类别。

浏览器读取样本（2026-10-26）：`sdu-rendered.html` 保留山大邮箱解密后仍存在的标记，`fudan-rendered.html` 保留复旦已加载名单和筛选按钮的结构；只保留回归所需的片段。

翻页回归样本：`input-image-pagination.html` 使用控件自身 `alt` 作为翻页标签；`image-link-pagination.html` 翻页只更新图片替代文字和个人页链接；`teacher-filter.html` 的空名单不能因 JavaScript 筛选链接而失去浏览器接管。这三份为最小构造样本，不请求外部资源。

`number-input-pagination.html` 在数字按钮前放同值的文本、数字和默认输入框，验证只重放可点按钮。`staggered-image-pagination.html` 每 600 毫秒更新一行 href／alt，验证材料停止变化后才读取整页。
