# 网页样本

2026-10-25 手动获取，只用于离线解析回归。各文件开头注明来源；保留实际卡片、链接和文字，删除无关脚本与样式。挑战页和动态名单保留识别所需的脚本，测试不执行它们。

| 文件 | 来源 | 验证场景 |
|---|---|---|
| `muc.html` | https://xingong.muc.edu.cn/szdw/xyjs.htm | 中央民族大学卡片、图片链接、`onclick` 导航 |
| `mcgill.html` | https://www.cs.mcgill.ca/people/faculty/ | 麦吉尔大学名单，每张卡片的姓名与邮箱归属 |
| `fudan.html` | https://software.fudan.edu.cn/zzjs/list.htm | 复旦大学未加载的动态教师列表 |
| `bupt.html` | https://scs.bupt.edu.cn/szjs1/jsyl.htm | 北京邮电大学 HTTP 412 挑战页 |
| `fzu.html` | https://chem.fzu.edu.cn/szdw/jsml1.htm | 福州大学图片链接中的 `mailto:` 联系方式 |
| `nju.html` | https://cs.nju.edu.cn/zhouzh/ | 南京大学真实个人页、中英文文字 |
| `nju-gbk.html` | 同上 | 从原 UTF-8 页面转码为 GBK，并修改编码声明，验证 GB2312／GBK 解码；不是网站原生编码的记录 |
| `seu.html` | https://cyber.seu.edu.cn/yjsds_22371/list.htm | 东南大学网址翻页和不可用的上一页控件 |
| `toronto.html` | https://www.cs.toronto.edu/~hinton/ | 多伦多大学真实个人页中的 `[at]`、`[dot]` 邮箱 |

Cloudflare 属性及错误输入等组合场景在测试中构造，不冒充真实网站样本。
