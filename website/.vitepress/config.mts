import { defineConfig } from "vitepress";

export default defineConfig({
  base: "/AutoEmailSender/",
  lang: "zh-CN",
  title: "Auto Email Sender",
  description: "面向导师联系场景的智能邮件助手",
  cleanUrls: true,
  head: [["link", { rel: "icon", href: "/AutoEmailSender/favicon.svg" }]],
  themeConfig: {
    logo: "/logo.svg",
    nav: [
      { text: "首页", link: "/" },
      { text: "文档", link: "/docs/getting-started" },
      { text: "抓取实测", link: "/crawl-benchmark" },
      { text: "致谢", link: "/acknowledgements" },
      { text: "下载", link: "https://github.com/JunieXD/AutoEmailSender/releases" },
      { text: "GitHub", link: "https://github.com/JunieXD/AutoEmailSender" }
    ],
    sidebar: {
      "/docs/": [
        {
          text: "开始使用",
          items: [
            { text: "快速开始", link: "/docs/getting-started" },
            { text: "安装桌面版", link: "/docs/install" },
            { text: "首次配置", link: "/docs/first-run" },
            { text: "个人中心", link: "/docs/profile" }
          ]
        },
        {
          text: "日常使用",
          items: [
            { text: "应用首页", link: "/docs/dashboard" },
            { text: "统计面板", link: "/docs/statistics" },
            { text: "导师管理", link: "/docs/mentors" },
            { text: "社区导师库", link: "/docs/community-mentors" },
            { text: "导师抓取 Skill", link: "/docs/mentor-crawler-skill" },
            { text: "匹配分析", link: "/docs/matching" },
            { text: "任务与工作区", link: "/docs/tasks-workspace" }
          ]
        },
        {
          text: "帮助",
          items: [
            { text: "交流与反馈", link: "/docs/feedback" },
            { text: "更新与常见问题", link: "/docs/faq" },
            { text: "致谢", link: "/acknowledgements" }
          ]
        },
        {
          text: "开发者文档",
          items: [{ text: "本地运行与打包", link: "/docs/developer" }]
        }
      ]
    },
    socialLinks: [{ icon: "github", link: "https://github.com/JunieXD/AutoEmailSender" }],
    footer: {
      message: "基于 GPL-3.0 许可证发布。",
      copyright: "Copyright © 2026 Auto Email Sender"
    },
    search: {
      provider: "local",
      options: {
        translations: {
          button: {
            buttonText: "搜索",
            buttonAriaLabel: "搜索"
          },
          modal: {
            displayDetails: "显示详细列表",
            resetButtonTitle: "清除搜索",
            backButtonTitle: "关闭搜索",
            noResultsText: "没有找到相关结果：",
            footer: {
              selectText: "选择",
              selectKeyAriaLabel: "回车键",
              navigateText: "切换",
              navigateUpKeyAriaLabel: "向上选择",
              navigateDownKeyAriaLabel: "向下选择",
              closeText: "关闭",
              closeKeyAriaLabel: "退出键"
            }
          }
        }
      }
    },
    darkModeSwitchLabel: "外观",
    lightModeSwitchTitle: "切换到浅色模式",
    darkModeSwitchTitle: "切换到深色模式",
    sidebarMenuLabel: "菜单",
    returnToTopLabel: "返回顶部",
    skipToContentLabel: "跳到正文",
    outline: {
      label: "页面导航"
    },
    docFooter: {
      prev: "上一页",
      next: "下一页"
    },
    lastUpdated: {
      text: "最后更新",
      formatOptions: {
        dateStyle: "short",
        timeStyle: "medium"
      }
    }
  }
});
