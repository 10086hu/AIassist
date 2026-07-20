using Microsoft.Win32;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;

namespace AiReportDesktop;

public partial class MainWindow : Window
{
    private readonly List<CheckBox> _checkBoxes = new();
    private Grid? _contentHost;
    private Grid? _reportPage;
    private UIElement? _recordsPage;
    private UIElement? _rulesPage;
    private UIElement? _settingsPage;
    private Button? _reportNavButton;
    private Button? _recordsNavButton;
    private Button? _rulesNavButton;
    private Button? _settingsNavButton;
    private TextBlock? _completenessStatusTextBlock;
    private TextBlock? _completenessSummaryTextBlock;
    private TextBlock? _reportTextCompletenessTextBlock;
    private TextBlock? _attachmentCompletenessTextBlock;
    private TextBlock? _tableCompletenessTextBlock;
    private TextBlock? _chapterCompletenessTextBlock;
    private bool _isCompletenessChecked;

    private readonly (string Title, string Description, string Badge)[] _checkItems =
    {
        ("建设依据审查", "根据上传的政策依据附件，结合可研中的依据表述，审查依据、附件内容与建设内容是否一致。", "依据"),
        ("重复建设检查", "筛查可研自身重复功能点，并支持与历史批复功能点清单进行比对。", "重复"),
        ("建设功能的对应关系检查", "检查建设内容、业务功能分析、系统功能需求分析、应用功能设计、功能点清单之间的对应关系。", "对应"),
        ("数据填报合理性", "检查用户量、业务量、并发数、高峰期用户、活跃用户比例、数据量、存储量和计算资源依据。", "数据"),
        ("资源申请的合理性", "检查云资源和三大件资源申请是否与系统功能、数据规模和性能需求匹配。", "资源"),
        ("安全内容的合理性", "检查安全需求分析与项目建设内容、安全建设内容之间的匹配性。", "安全"),
        ("价格合理性", "根据软件造价标准拆解功能点，形成可复核的核价结果入口。", "造价"),
        ("软硬件产品价格参考", "展示政采网、住建部询价网和价格数据的参考价信息。", "询价"),
        ("敏感词检测", "识别报告中不符合要求的词语、表达等敏感或违规内容。", "敏感")
    };

    public MainWindow()
    {
        InitializeComponent();
        PrepareText();
        PrepareNavigation();
        BuildCheckItems();
    }

    private void PrepareText()
    {
        foreach (var textBlock in FindChildren<TextBlock>(this))
        {
            if (textBlock.Text == "桌面前端页面完成版")
            {
                textBlock.Text = "可研报告智能审查助手";
            }
        }
    }
    private void PrepareNavigation()
    {
        _reportPage = WorkbenchPage.Children.OfType<Grid>().First(item => Grid.GetColumn(item) == 1);
        _contentHost = new Grid { Margin = _reportPage.Margin };
        Grid.SetColumn(_contentHost, 1);
        _reportPage.Margin = new Thickness(0);
        WorkbenchPage.Children.Remove(_reportPage);
        WorkbenchPage.Children.Add(_contentHost);
        _contentHost.Children.Add(_reportPage);
        InsertCompletenessPrecheck();

        _recordsPage = BuildRecordsPage();
        _rulesPage = BuildRulesPage();
        _settingsPage = BuildSettingsPage();

        _reportNavButton = FindNavButton("可研报告检测", "Report");
        _recordsNavButton = FindNavButton("审查记录", "Records");
        _rulesNavButton = FindNavButton("规则库配置", "Rules");
        _settingsNavButton = FindNavButton("系统设置", "Settings");

        if (_rulesNavButton != null)
        {
            _rulesNavButton.Content = "规则库配置";
        }

        foreach (var button in new[] { _reportNavButton, _recordsNavButton, _rulesNavButton, _settingsNavButton })
        {
            if (button != null)
            {
                button.Click += OnNavClicked;
            }
        }

        ShowPage("Report");
    }

    private Button? FindNavButton(string content, string tag)
    {
        var button = FindChildren<Button>(WorkbenchPage).FirstOrDefault(item => item.Content?.ToString() == content);
        if (button != null)
        {
            button.Tag = tag;
        }

        return button;
    }

    private void OnNavClicked(object sender, RoutedEventArgs e)
    {
        if (sender is Button button && button.Tag is string tag)
        {
            ShowPage(tag);
        }
    }

    private void ShowPage(string tag)
    {
        if (_contentHost == null || _reportPage == null)
        {
            return;
        }

        _contentHost.Children.Clear();
        _contentHost.Children.Add(tag switch
        {
            "Records" => _recordsPage!,
            "Rules" => _rulesPage!,
            "Settings" => _settingsPage!,
            _ => _reportPage
        });

        foreach (var button in new[] { _reportNavButton, _recordsNavButton, _rulesNavButton, _settingsNavButton })
        {
            if (button != null)
            {
                button.Style = (Style)FindResource(button.Tag?.ToString() == tag ? "ActiveNavButton" : "NavButton");
            }
        }
    }

    private void InsertCompletenessPrecheck()
    {
        if (_reportPage == null)
        {
            return;
        }

        foreach (UIElement child in _reportPage.Children)
        {
            var row = Grid.GetRow(child);
            if (row >= 1)
            {
                Grid.SetRow(child, row + 1);
            }
        }

        _reportPage.RowDefinitions.Insert(1, new RowDefinition { Height = GridLength.Auto });
        var section = BuildCompletenessPrecheck();
        Grid.SetRow(section, 1);
        _reportPage.Children.Add(section);
    }

    private Border BuildCompletenessPrecheck()
    {
        var root = new Grid();
        root.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.35, GridUnitType.Star) });
        root.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

        var left = new StackPanel();
        left.Children.Add(Text("前置完整性检查", 18, FontWeights.Bold, FindBrush("TextBrush")));
        left.Children.Add(Text("检查可研报告文本、附件材料、表格数据和关键章节是否齐全。该项作为专项检测前置项，完成后再进入九项专项检测。", 14, null, FindBrush("MutedBrush"), new Thickness(0, 6, 0, 12), true));
        var tags = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        tags.Children.Add(Pill("前置项", Brush(37, 99, 235), Brush(239, 246, 255)));
        tags.Children.Add(Pill("材料基础", Brush(2, 122, 72), Brush(236, 253, 243)));
        tags.Children.Add(Pill("结果可查看", Brush(181, 71, 8), Brush(255, 250, 235)));
        left.Children.Add(tags);
        var runButton = Button("执行完整性检查", true);
        runButton.Click += OnRunCompletenessCheckClicked;
        left.Children.Add(ActionRow(runButton, CompletenessResultButton()));
        root.Children.Add(left);

        var right = new StackPanel { Margin = new Thickness(20, 0, 0, 0) };
        Grid.SetColumn(right, 1);
        var statusHeader = new Grid();
        statusHeader.ColumnDefinitions.Add(new ColumnDefinition());
        statusHeader.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        statusHeader.Children.Add(Text("完整性检查结果", 16, FontWeights.SemiBold, FindBrush("TextBrush")));
        _completenessStatusTextBlock = Text("未检查", 13, FontWeights.SemiBold, Brush(181, 71, 8));
        Grid.SetColumn(_completenessStatusTextBlock, 1);
        statusHeader.Children.Add(_completenessStatusTextBlock);
        right.Children.Add(statusHeader);
        right.Children.Add(CompletenessItem("报告文本", "未检查", Brush(181, 71, 8), item => _reportTextCompletenessTextBlock = item));
        right.Children.Add(CompletenessItem("附件材料", "未检查", Brush(181, 71, 8), item => _attachmentCompletenessTextBlock = item));
        right.Children.Add(CompletenessItem("表格数据", "未检查", Brush(181, 71, 8), item => _tableCompletenessTextBlock = item));
        right.Children.Add(CompletenessItem("关键章节", "未检查", Brush(181, 71, 8), item => _chapterCompletenessTextBlock = item));
        _completenessSummaryTextBlock = Text("请选择报告后执行完整性检查。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 8, 0, 0), true);
        right.Children.Add(_completenessSummaryTextBlock);
        root.Children.Add(right);

        return Card(root, new Thickness(18, 16, 18, 16), new Thickness(0, 16, 0, 0));
    }

    private Button CompletenessResultButton()
    {
        var button = Button("查看完整性结果", false);
        button.Click += OnViewCompletenessResultClicked;
        return button;
    }

    private Border CompletenessItem(string title, string status, Brush statusBrush, Action<TextBlock> capture)
    {
        var row = new Grid { Margin = new Thickness(0, 10, 0, 0) };
        row.ColumnDefinitions.Add(new ColumnDefinition());
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.Children.Add(Text(title, 13, FontWeights.SemiBold, FindBrush("TextBrush")));
        var statusText = Text(status, 13, FontWeights.SemiBold, statusBrush);
        capture(statusText);
        Grid.SetColumn(statusText, 1);
        row.Children.Add(statusText);
        return new Border { BorderBrush = Brush(234, 236, 240), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 0, 0, 8), Child = row };
    }

    private void OnRunCompletenessCheckClicked(object sender, RoutedEventArgs e)
    {
        if (ReportPathTextBlock.Text == "尚未选择可研报告")
        {
            MessageBox.Show(this, "请先选择可研报告文件，再执行完整性检查。", "缺少报告", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        _isCompletenessChecked = true;
        SetCompletenessText(_completenessStatusTextBlock, "已完成", Brush(2, 122, 72));
        SetCompletenessText(_reportTextCompletenessTextBlock, "齐全", Brush(2, 122, 72));
        SetCompletenessText(_attachmentCompletenessTextBlock, "齐全", Brush(2, 122, 72));
        SetCompletenessText(_tableCompletenessTextBlock, "需复核", Brush(181, 71, 8));
        SetCompletenessText(_chapterCompletenessTextBlock, "齐全", Brush(2, 122, 72));
        if (_completenessSummaryTextBlock != null)
        {
            _completenessSummaryTextBlock.Text = "完整性检查完成：报告文本、附件材料和关键章节齐全，表格数据建议复核后继续专项检测。";
            _completenessSummaryTextBlock.Foreground = Brush(71, 84, 103);
        }

        StatusTextBlock.Text = "前置完整性检查已完成，可继续选择专项检测范围。";
    }

    private void OnViewCompletenessResultClicked(object sender, RoutedEventArgs e)
    {
        var message = _isCompletenessChecked
            ? "完整性检查结果：报告文本齐全；附件材料齐全；表格数据建议复核；关键章节齐全。"
            : "完整性检查尚未执行。请先选择报告并点击“执行完整性检查”。";
        MessageBox.Show(this, message, "完整性检查结果", MessageBoxButton.OK, MessageBoxImage.Information);
    }

    private void SetCompletenessText(TextBlock? textBlock, string text, Brush brush)
    {
        if (textBlock == null)
        {
            return;
        }

        textBlock.Text = text;
        textBlock.Foreground = brush;
    }
    private void BuildCheckItems()
    {
        CheckItemsGrid.Children.Clear();
        _checkBoxes.Clear();

        foreach (var item in _checkItems)
        {
            var border = new Border
            {
                Background = Brushes.White,
                BorderBrush = new SolidColorBrush(Color.FromRgb(216, 222, 233)),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(12),
                Padding = new Thickness(16, 14, 16, 14),
                Margin = new Thickness(0, 0, 12, 12)
            };

            var stack = new StackPanel();
            var header = new DockPanel { LastChildFill = true };
            var badge = new Border
            {
                Background = new SolidColorBrush(Color.FromRgb(239, 246, 255)),
                BorderBrush = new SolidColorBrush(Color.FromRgb(191, 219, 254)),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(12),
                Padding = new Thickness(10, 4, 10, 4),
                Margin = new Thickness(0, 0, 10, 0),
                Child = Text(item.Badge, 14, FontWeights.Bold, Brush(29, 78, 216))
            };
            DockPanel.SetDock(badge, Dock.Left);
            header.Children.Add(badge);

            var checkBox = new CheckBox
            {
                Content = item.Title,
                IsChecked = true,
                FontWeight = FontWeights.SemiBold,
                FontSize = 14,
                VerticalAlignment = VerticalAlignment.Center
            };
            header.Children.Add(checkBox);
            _checkBoxes.Add(checkBox);

            stack.Children.Add(header);
            stack.Children.Add(Text(item.Description, 14, null, Brush(102, 112, 133), new Thickness(0, 12, 0, 0), true));
            border.Child = stack;
            CheckItemsGrid.Children.Add(border);
        }
    }

    private Grid BuildRecordsPage()
    {
        var page = PageGrid(3);
        page.RowDefinitions[2].Height = new GridLength(1, GridUnitType.Star);
        var header = Header("审查记录", "查看报告审查任务、执行状态和结果摘要。", "刷新记录", false);
        page.Children.Add(header);

        var stats = new UniformGrid { Columns = 3, Margin = new Thickness(0, 18, 0, 0) };
        Grid.SetRow(stats, 1);
        stats.Children.Add(StatCard("3", "全部记录", "本地工作台任务汇总", Brush(37, 99, 235)));
        stats.Children.Add(StatCard("1", "已完成", "可查看结果摘要", Brush(2, 122, 72)));
        stats.Children.Add(StatCard("2", "待处理", "等待确认审查范围", Brush(181, 71, 8)));
        page.Children.Add(stats);

        var table = Card(new Grid(), new Thickness(0), new Thickness(0, 18, 0, 0));
        Grid.SetRow(table, 2);
        var grid = (Grid)table.Child;
        for (var i = 0; i < 4; i++) grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        AddRecordHeader(grid);
        AddRecordRow(grid, 1, "智慧园区可研报告.docx", "九项审查", "已完成", "2026-07-09", "发现 4 项需复核内容", Brush(2, 122, 72));
        AddRecordRow(grid, 2, "数据中心扩容方案.pdf", "资源、价格、安全", "待处理", "2026-07-09", "已保存任务参数", Brush(181, 71, 8));
        AddRecordRow(grid, 3, "业务系统升级可研.xlsx", "数据、敏感词", "待确认", "2026-07-08", "等待补充报告材料", Brush(23, 92, 211));
        page.Children.Add(table);
        return page;
    }

    private Grid BuildRulesPage()
    {
        var page = PageGrid(3);
        page.RowDefinitions[2].Height = new GridLength(1, GridUnitType.Star);
        page.Children.Add(Header("规则库配置", "治理审查规则、版本、适用范围与复核策略。", "新增规则", true));

        var stats = new UniformGrid { Columns = 4, Margin = new Thickness(0, 18, 0, 0) };
        Grid.SetRow(stats, 1);
        stats.Children.Add(StatCard("42", "启用规则", "覆盖九项审查目录", Brush(37, 99, 235)));
        stats.Children.Add(StatCard("6", "待复核", "规则变更需确认", Brush(181, 71, 8)));
        stats.Children.Add(StatCard("92%", "命中可信度", "近 30 次任务均值", Brush(2, 122, 72)));
        stats.Children.Add(StatCard("v1.0", "当前版本", "2026-07-09 更新", Brush(52, 64, 84)));
        page.Children.Add(stats);

        var body = new Grid { Margin = new Thickness(0, 18, 0, 0) };
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.05, GridUnitType.Star) });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.65, GridUnitType.Star) });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.05, GridUnitType.Star) });
        Grid.SetRow(body, 2);

        body.Children.Add(BuildRuleCategories());
        var editor = BuildRuleEditor();
        Grid.SetColumn(editor, 1);
        body.Children.Add(editor);

        var govern = new StackPanel();
        govern.Children.Add(Text("治理状态", 18, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(0, 0, 0, 6)));
        govern.Children.Add(Text("关注规则变更、质量与发布节奏。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 14), true));
        govern.Children.Add(StatusMini("版本状态", "v1.0 当前生效", Brush(37, 99, 235)));
        govern.Children.Add(StatusMini("质量检查", "2 条规则需补样例", Brush(181, 71, 8)));
        govern.Children.Add(StatusMini("最近发布", "2026-07-09 09:30", Brush(52, 64, 84)));
        govern.Children.Add(Text("阈值策略", 16, FontWeights.SemiBold, FindBrush("TextBrush"), new Thickness(0, 18, 0, 10)));
        govern.Children.Add(Text("高风险  90+", 13, FontWeights.SemiBold, Brush(180, 35, 24), new Thickness(0, 0, 0, 8)));
        govern.Children.Add(Text("需复核  70-89", 13, FontWeights.SemiBold, Brush(181, 71, 8), new Thickness(0, 0, 0, 8)));
        govern.Children.Add(Text("提示项  50-69", 13, FontWeights.SemiBold, Brush(37, 99, 235), new Thickness(0, 0, 0, 18)));
        govern.Children.Add(Text("发布清单", 16, FontWeights.SemiBold, FindBrush("TextBrush"), new Thickness(0, 0, 0, 10)));
        govern.Children.Add(Check("规则说明完整", true));
        govern.Children.Add(Check("样例材料已覆盖", false));
        govern.Children.Add(Check("复核等级已确认", true));
        var governCard = Card(govern, new Thickness(18), new Thickness(0));
        Grid.SetColumn(governCard, 2);
        body.Children.Add(governCard);

        page.Children.Add(body);
        return page;
    }
    private Grid BuildSettingsPage()
    {
        var page = PageGrid(3);
        page.RowDefinitions[2].Height = new GridLength(1, GridUnitType.Star);
        page.Children.Add(Header("系统设置", "统一配置客户端运行、文件保存、审查偏好和服务连接。", "保存设置", true));

        var summary = new UniformGrid { Columns = 4, Margin = new Thickness(0, 18, 0, 0) };
        Grid.SetRow(summary, 1);
        summary.Children.Add(StatCard("D 盘", "工作目录", "D:\\AIassist\\data", Brush(37, 99, 235)));
        summary.Children.Add(StatCard("100%", "界面缩放", "适配当前显示器", Brush(52, 64, 84)));
        summary.Children.Add(StatCard("60 秒", "请求超时", "适合大文件审查", Brush(2, 122, 72)));
        summary.Children.Add(StatCard("开启", "操作留痕", "记录规则与任务变更", Brush(181, 71, 8)));
        page.Children.Add(summary);

        var body = new Grid { Margin = new Thickness(0, 18, 0, 0) };
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.34, GridUnitType.Star) });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        Grid.SetRow(body, 2);

        var left = new StackPanel { Margin = new Thickness(0, 0, 14, 0) };
        left.Children.Add(SettingsPanel("工作区与文件", "管理报告材料、结果文件和临时缓存的位置。", new UIElement[]
        {
            SettingRow("默认工作目录", "报告、结果和临时文件的统一保存位置", Input("D:\\AIassist\\data")),
            SettingRow("结果命名规则", "决定导出报告的文件命名方式", Combo("项目名称 + 审查日期", "报告原名 + 版本号", "自定义前缀")),
            SettingRow("保存最近目录", "下次选择文件时自动定位到上次目录", Check("启用", true)),
            SettingRow("完成后打开目录", "任务完成后便于快速核对输出文件", Check("启用", false))
        }));
        left.Children.Add(SettingsPanel("审查偏好", "定义任务创建时的默认范围和结果呈现方式。", new UIElement[]
        {
            SettingRow("默认审查范围", "新建任务时自动勾选完整审查范围", Check("九项全选", true)),
            SettingRow("结果摘要", "审查记录中显示问题数量和复核建议", Check("生成摘要", true)),
            SettingRow("问题等级展示", "控制结果页默认展开的严重程度", Combo("显示全部", "仅重要及以上", "仅严重问题"))
        }, new Thickness(0, 14, 0, 0)));
        body.Children.Add(left);

        var right = new StackPanel { Margin = new Thickness(14, 0, 0, 0) };
        Grid.SetColumn(right, 1);
        right.Children.Add(SettingsPanel("服务连接", "配置客户端访问审查服务的地址和响应策略。", new UIElement[]
        {
            SettingRow("服务地址", "当前客户端请求入口", Input("http://127.0.0.1:8000/api")),
            SettingRow("请求超时", "大文件或复杂规则审查建议使用 60 秒以上", Combo("30 秒", "60 秒", "120 秒")),
            ActionRow(Button("测试连接", false))
        }));
        right.Children.Add(SettingsPanel("安全与日志", "控制本机操作记录、脱敏和诊断信息。", new UIElement[]
        {
            SettingRow("规则变更记录", "保存规则变更人、时间和说明", Check("启用", true)),
            SettingRow("结果摘要脱敏", "列表中隐藏报告路径和敏感字段", Check("启用", true)),
            SettingRow("日志保留周期", "超过周期的客户端日志可清理", Combo("30 天", "90 天", "180 天"))
        }, new Thickness(0, 14, 0, 0)));
        body.Children.Add(right);
        page.Children.Add(body);
        return page;
    }
    private Border BuildRuleCategories()
    {
        var stack = new StackPanel();
        stack.Children.Add(Text("规则目录", 18, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(0, 0, 0, 6)));
        stack.Children.Add(Text("按审查场景组织，快速定位规则集。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 14), true));
        stack.Children.Add(RuleItem("建设依据审查", "8 条规则", "已启用", Brush(37, 99, 235), true));
        stack.Children.Add(RuleItem("重复建设检查", "5 条规则", "需复核", Brush(181, 71, 8), false));
        stack.Children.Add(RuleItem("功能对应关系", "7 条规则", "已启用", Brush(2, 122, 72), false));
        stack.Children.Add(RuleItem("数据填报合理性", "6 条规则", "已启用", Brush(2, 122, 72), false));
        stack.Children.Add(RuleItem("资源申请合理性", "5 条规则", "已启用", Brush(2, 122, 72), false));
        stack.Children.Add(RuleItem("安全内容合理性", "4 条规则", "已启用", Brush(2, 122, 72), false));
        stack.Children.Add(RuleItem("价格合理性", "4 条规则", "需复核", Brush(181, 71, 8), false));
        stack.Children.Add(RuleItem("产品价格参考", "2 条规则", "已启用", Brush(2, 122, 72), false));
        stack.Children.Add(RuleItem("敏感词检测", "4 条规则", "已启用", Brush(2, 122, 72), false));
        return Card(stack, new Thickness(16), new Thickness(0, 0, 14, 0));
    }

    private Border BuildRuleEditor()
    {
        var stack = new StackPanel();
        stack.Children.Add(Text("建设依据审查", 18, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(0, 0, 0, 6)));
        stack.Children.Add(Text("核对政策依据、批复文件与建设内容的一致性。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 14), true));

        var chips = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        chips.Children.Add(Pill("启用中", Brush(2, 122, 72), Brush(236, 253, 243)));
        chips.Children.Add(Pill("8 条规则", Brush(37, 99, 235), Brush(239, 246, 255)));
        chips.Children.Add(Pill("重要等级", Brush(181, 71, 8), Brush(255, 250, 235)));
        stack.Children.Add(chips);

        stack.Children.Add(RuleClause("依据文件完整性", "检查报告引用的政策、批复和附件是否在材料清单中存在。", "缺失材料时标记为重要问题", true));
        stack.Children.Add(RuleClause("依据内容一致性", "比对建设内容、投资范围与依据文件中的授权边界。", "发现不一致时要求人工复核", true));
        stack.Children.Add(RuleClause("引用时效性", "识别失效、过期或被替代的政策依据。", "优先提示最新有效依据", false));

        var notes = new Border
        {
            Background = Brush(248, 250, 252),
            BorderBrush = Brush(216, 222, 233),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(10),
            Padding = new Thickness(14),
            Margin = new Thickness(0, 6, 0, 0),
            Child = Text("复核建议：当规则同时命中“依据缺失”和“建设内容超范围”时，结果摘要中提升为重点复核项。", 14, null, Brush(71, 84, 103), null, true)
        };
        stack.Children.Add(notes);
        var actions = ActionRow(Button("保存规则集", true), Button("复制为新版本", false));
        actions.Margin = new Thickness(0, 16, 0, 0);
        stack.Children.Add(actions);
        return Card(stack, new Thickness(18), new Thickness(0, 0, 14, 0));
    }

    private Border RuleItem(string title, string count, string status, Brush accent, bool selected)
    {
        var row = new Grid();
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(4) });
        row.ColumnDefinitions.Add(new ColumnDefinition());
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.Children.Add(new Border { Background = accent, CornerRadius = new CornerRadius(3), Margin = new Thickness(0, 2, 12, 2) });
        var copy = new StackPanel { Margin = new Thickness(12, 0, 0, 0) };
        copy.Children.Add(Text(title, 14, FontWeights.SemiBold, FindBrush("TextBrush")));
        copy.Children.Add(Text(count, 12, null, FindBrush("MutedBrush"), new Thickness(0, 3, 0, 0)));
        Grid.SetColumn(copy, 1);
        row.Children.Add(copy);
        var tag = Pill(status, accent, selected ? Brush(239, 246, 255) : Brush(248, 250, 252));
        tag.VerticalAlignment = VerticalAlignment.Center;
        Grid.SetColumn(tag, 2);
        row.Children.Add(tag);
        return new Border { Background = selected ? Brush(239, 246, 255) : Brushes.White, BorderBrush = selected ? Brush(191, 219, 254) : Brush(234, 236, 240), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Padding = new Thickness(12), Margin = new Thickness(0, 0, 0, 10), Child = row };
    }

    private Border RuleClause(string title, string description, string result, bool enabled)
    {
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var copy = new StackPanel();
        copy.Children.Add(Text(title, 15, FontWeights.SemiBold, FindBrush("TextBrush")));
        copy.Children.Add(Text(description, 13, null, FindBrush("MutedBrush"), new Thickness(0, 4, 0, 0), true));
        copy.Children.Add(Text(result, 13, null, Brush(71, 84, 103), new Thickness(0, 8, 0, 0), true));
        grid.Children.Add(copy);
        var check = new CheckBox { IsChecked = enabled, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(14, 2, 0, 0) };
        Grid.SetColumn(check, 1);
        grid.Children.Add(check);
        return new Border { Background = Brushes.White, BorderBrush = Brush(234, 236, 240), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Padding = new Thickness(14), Margin = new Thickness(0, 0, 0, 10), Child = grid };
    }

    private Border Pill(string text, Brush foreground, Brush background)
    {
        return new Border { Background = background, BorderBrush = Brush(234, 236, 240), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(12), Padding = new Thickness(8, 3, 8, 3), Margin = new Thickness(0, 0, 8, 6), Child = Text(text, 12, FontWeights.SemiBold, foreground) };
    }

    private Border StatusMini(string label, string value, Brush accent)
    {
        var grid = new Grid { Margin = new Thickness(0, 0, 0, 10) };
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.Children.Add(Text(label, 13, null, FindBrush("MutedBrush")));
        var valueText = Text(value, 13, FontWeights.SemiBold, accent);
        Grid.SetColumn(valueText, 1);
        grid.Children.Add(valueText);
        return new Border { BorderBrush = Brush(234, 236, 240), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 0, 0, 10), Child = grid };
    }
    private Border SettingsPanel(string title, string subtitle, IEnumerable<UIElement> children, Thickness? margin = null)
    {
        var stack = new StackPanel();
        stack.Children.Add(Text(title, 18, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(0, 0, 0, 5)));
        stack.Children.Add(Text(subtitle, 13, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 10), true));
        foreach (var child in children)
        {
            stack.Children.Add(child);
        }

        return Card(stack, new Thickness(18), margin ?? new Thickness(0));
    }

    private Border SettingRow(string title, string description, UIElement control)
    {
        if (control is FrameworkElement element)
        {
            element.Margin = new Thickness(0);
            element.MinWidth = 190;
        }

        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(230) });
        var copy = new StackPanel();
        copy.Children.Add(Text(title, 14, FontWeights.SemiBold, FindBrush("TextBrush")));
        copy.Children.Add(Text(description, 12, null, FindBrush("MutedBrush"), new Thickness(0, 4, 18, 0), true));
        grid.Children.Add(copy);
        Grid.SetColumn(control, 1);
        grid.Children.Add(control);
        return new Border { BorderBrush = Brush(234, 236, 240), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 12, 0, 12), Child = grid };
    }
    private Grid PageGrid(int rows)
    {
        var grid = new Grid();
        for (var i = 0; i < rows; i++)
        {
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        }

        return grid;
    }

    private Grid Header(string title, string subtitle, string buttonText, bool primaryButton)
    {
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var stack = new StackPanel();
        stack.Children.Add(Text(title, 28, FontWeights.Bold, FindBrush("TextBrush")));
        stack.Children.Add(Text(subtitle, 14, null, FindBrush("MutedBrush"), new Thickness(0, 6, 0, 0)));
        grid.Children.Add(stack);
        var button = Button(buttonText, primaryButton);
        button.VerticalAlignment = VerticalAlignment.Center;
        Grid.SetColumn(button, 1);
        grid.Children.Add(button);
        return grid;
    }

    private Border StatCard(string value, string title, string subtitle, Brush valueBrush)
    {
        var stack = new StackPanel();
        stack.Children.Add(Text(value, 30, FontWeights.Bold, valueBrush));
        stack.Children.Add(Text(title, 17, FontWeights.SemiBold, FindBrush("TextBrush")));
        stack.Children.Add(Text(subtitle, 14, null, FindBrush("MutedBrush"), new Thickness(0, 4, 0, 0)));
        return Card(stack, new Thickness(18, 14, 18, 14), new Thickness(0, 0, 12, 0));
    }

    private Border SettingsCard(string title, IEnumerable<UIElement> children, Thickness? margin = null)
    {
        var stack = new StackPanel();
        stack.Children.Add(Text(title, 18, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(0, 0, 0, 14)));
        foreach (var child in children)
        {
            stack.Children.Add(child);
        }

        return Card(stack, new Thickness(18), margin ?? new Thickness(0));
    }

    private void AddRecordHeader(Grid grid)
    {
        var row = RecordRow("报告名称", "审查范围", "状态", "创建时间", "结果摘要", true, Brushes.Transparent);
        row.Background = Brush(248, 250, 252);
        grid.Children.Add(row);
    }

    private void AddRecordRow(Grid grid, int rowIndex, string name, string range, string status, string time, string summary, Brush statusBrush)
    {
        var row = RecordRow(name, range, status, time, summary, false, statusBrush);
        if (rowIndex == 2)
        {
            row.Background = Brush(252, 252, 253);
        }
        Grid.SetRow(row, rowIndex);
        grid.Children.Add(row);
    }

    private Grid RecordRow(string name, string range, string status, string time, string summary, bool header, Brush statusBrush)
    {
        var row = new Grid();
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(2, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.3, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.2, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.2, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(2, GridUnitType.Star) });
        AddCell(row, name, 0, header, FindBrush("TextBrush"));
        AddCell(row, range, 1, header, FindBrush("TextBrush"));
        AddCell(row, status, 2, true, header ? FindBrush("TextBrush") : statusBrush);
        AddCell(row, time, 3, header, FindBrush("TextBrush"));
        AddCell(row, summary, 4, header, FindBrush("TextBrush"));
        return row;
    }

    private void AddCell(Grid row, string value, int column, bool semiBold, Brush brush)
    {
        var cell = Text(value, 14, semiBold ? FontWeights.SemiBold : null, brush, new Thickness(18, 14, 18, 14), true);
        Grid.SetColumn(cell, column);
        row.Children.Add(cell);
    }

    private TextBlock Label(string value) => Text(value, 14, null, Brush(52, 64, 84), new Thickness(0, 0, 0, 7));

    private TextBox Input(string value) => new()
    {
        Text = value,
        Style = (Style)FindResource("InputBox"),
        Margin = new Thickness(0, 0, 0, 14)
    };

    private ComboBox Combo(params string[] values)
    {
        var combo = new ComboBox { Padding = new Thickness(10), Margin = new Thickness(0, 0, 0, 14), SelectedIndex = values.Length > 1 ? 1 : 0 };
        foreach (var value in values)
        {
            combo.Items.Add(new ComboBoxItem { Content = value });
        }

        return combo;
    }

    private CheckBox Check(string value, bool isChecked, Thickness? margin = null) => new()
    {
        Content = value,
        IsChecked = isChecked,
        Margin = margin ?? new Thickness(0, 0, 0, 10)
    };

    private Button Button(string text, bool primary)
    {
        return new Button
        {
            Content = text,
            Style = (Style)FindResource(primary ? "PrimaryButton" : "SecondaryButton"),
            Margin = primary ? new Thickness(0, 0, 8, 0) : new Thickness(0)
        };
    }

    private StackPanel ActionRow(params UIElement[] children)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        foreach (var child in children)
        {
            row.Children.Add(child);
        }

        return row;
    }

    private Border Card(UIElement child, Thickness padding, Thickness margin) => new()
    {
        Style = (Style)FindResource("CardBorder"),
        Padding = padding,
        Margin = margin,
        Child = child
    };

    private TextBlock Text(string value, double size, FontWeight? weight = null, Brush? brush = null, Thickness? margin = null, bool wrap = false)
    {
        return new TextBlock
        {
            Text = value,
            FontSize = size,
            FontWeight = weight ?? FontWeights.Normal,
            Foreground = brush ?? FindBrush("TextBrush"),
            Margin = margin ?? new Thickness(0),
            TextWrapping = wrap ? TextWrapping.Wrap : TextWrapping.NoWrap,
            LineHeight = wrap ? 22 : double.NaN
        };
    }

    private Brush FindBrush(string key) => (Brush)FindResource(key);

    private static SolidColorBrush Brush(byte r, byte g, byte b) => new(Color.FromRgb(r, g, b));

    private static IEnumerable<T> FindChildren<T>(DependencyObject parent) where T : DependencyObject
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
        {
            var child = VisualTreeHelper.GetChild(parent, i);
            if (child is T match)
            {
                yield return match;
            }

            foreach (var descendant in FindChildren<T>(child))
            {
                yield return descendant;
            }
        }
    }

    private void OnLoginModeChanged(object sender, RoutedEventArgs e)
    {
        if (RegisterFields == null || EnterButton == null)
        {
            return;
        }

        var isRegister = RegisterModeButton.IsChecked == true;
        RegisterFields.Visibility = isRegister ? Visibility.Visible : Visibility.Collapsed;
        EnterButton.Content = isRegister ? "注册并进入系统" : "登录并进入系统";
    }

    private void OnEnterClicked(object sender, RoutedEventArgs e)
    {
        var userName = string.IsNullOrWhiteSpace(AccountTextBox.Text) ? "演示用户" : AccountTextBox.Text.Trim();
        UserTextBlock.Text = $"当前用户：{userName}";
        LoginPage.Visibility = Visibility.Collapsed;
        WorkbenchPage.Visibility = Visibility.Visible;
    }

    private void OnLogoutClicked(object sender, RoutedEventArgs e)
    {
        WorkbenchPage.Visibility = Visibility.Collapsed;
        LoginPage.Visibility = Visibility.Visible;
    }

    private void OnChooseFileClicked(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog
        {
            Title = "选择可研报告",
            Filter = "报告文件|*.docx;*.doc;*.pdf;*.xlsx;*.xls|所有文件|*.*"
        };

        if (dialog.ShowDialog(this) == true)
        {
            ReportPathTextBlock.Text = dialog.FileName;
            StatusTextBlock.Text = "已选择报告，请确认需要检测的审查项。";
        }
    }

    private void OnSelectAllClicked(object sender, RoutedEventArgs e)
    {
        foreach (var checkBox in _checkBoxes)
        {
            checkBox.IsChecked = true;
        }

        StatusTextBlock.Text = "已选择全部九项检测。";
    }

    private void OnClearAllClicked(object sender, RoutedEventArgs e)
    {
        foreach (var checkBox in _checkBoxes)
        {
            checkBox.IsChecked = false;
        }

        StatusTextBlock.Text = "已清空检测范围。";
    }

    private void OnStartCheckClicked(object sender, RoutedEventArgs e)
    {
        if (ReportPathTextBlock.Text == "尚未选择可研报告")
        {
            MessageBox.Show(this, "请先选择可研报告文件。", "缺少报告", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        if (!_isCompletenessChecked)
        {
            MessageBox.Show(this, "请先完成前置完整性检查，再创建专项检测任务。", "完整性检查未完成", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        var selectedCount = _checkBoxes.Count(item => item.IsChecked == true);
        if (selectedCount == 0)
        {
            MessageBox.Show(this, "请至少选择一个检测项。", "缺少检测项", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        StatusTextBlock.Text = $"已创建检测任务：{selectedCount} 项。请在审查记录中查看任务状态和结果摘要。";
    }
}











