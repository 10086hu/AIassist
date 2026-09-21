using Microsoft.Win32;
using System.IO;
using System.Globalization;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Encodings.Web;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using Microsoft.Web.WebView2.Wpf;

namespace AiReportDesktop;

public partial class MainWindow : Window
{
    private const string BackendBaseUrl = "http://127.0.0.1:8000/api";
    private static readonly HttpClient BackendClient = new() { Timeout = TimeSpan.FromMinutes(10) };

    private readonly List<CheckBox> _checkBoxes = new();
    private readonly Dictionary<string, List<CheckBox>> _ruleCheckBoxesByModule = new();
    private readonly StackPanel RulesListPanel = new();
    private readonly StackPanel ResultsSummaryPanel = new();
    private readonly StackPanel ResultsListPanel = new();
    private readonly Dictionary<string, List<RuleDisplayItem>> _rulesByModule = new();
    private Grid? _contentHost;
    private Grid? _reportPage;
    private Grid? _specialistPage;
    private UIElement? _projectSelectionCard;
    private UIElement? _reportSelectionCard;
    private UIElement? _recordsPage;
    private UIElement? _rulesPage;
    private UIElement? _settingsPage;
    private StackPanel? _projectTreePanel;
    private Button? _deleteSelectionButton;
    private StackPanel? _recordDetailPanel;
    private WebView2? _documentWebView;
    private TextBlock? _documentTitleTextBlock;
    private TextBlock? _documentLocationTextBlock;
    private string? _activePreviewUrl;
    private string? _activePreviewExtension;
    private readonly Dictionary<string, string> _previewUrlsByRole = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, string> _previewNamesByRole = new(StringComparer.OrdinalIgnoreCase);
    private StackPanel? _rulesDirectoryPanel;
    private StackPanel? _rulesEditorPanel;
    private TextBlock? _ruleStatsTextBlock;
    private string _activeRuleModule = "function_correspondence";
    private string? _activeRecordId;
    private Button? _reportNavButton;
    private Button? _specialistNavButton;
    private Button? _recordsNavButton;
    private Button? _rulesNavButton;
    private Button? _settingsNavButton;
    private TextBlock? _completenessStatusTextBlock;
    private TextBlock? _completenessSummaryTextBlock;
    private TextBlock? _reportTextCompletenessTextBlock;
    private TextBlock? _attachmentCompletenessTextBlock;
    private TextBlock? _tableCompletenessTextBlock;
    private TextBlock? _chapterCompletenessTextBlock;
    private TextBlock? _priceBenchmarkStatusTextBlock;
    private PasswordBox? _llmApiKeyBox;
    private TextBox? _llmBaseUrlBox;
    private TextBox? _llmModelBox;
    private TextBlock? _llmConfigStatusTextBlock;
    private CheckBox? _llmReviewCheckBox;
    private bool _isCompletenessChecked;
    private bool _isChecking;
    private List<string> _duplicateHistoryFiles = new();
    private bool _duplicateHistoryChoiceCaptured;
    private bool _isUpdatingDuplicateSelection;
    private string? _selectedProjectId;
    private string? _selectedProjectName;
    private string? _selectedCheckRunId;
    private readonly Dictionary<string, List<RunResultInfo>> _runResults = new();

    private readonly CheckItem[] _checkItems =
    {
        new("建设依据审查", "basis", "根据上传的政策依据附件，结合可研中的依据表述，审查依据、附件内容与建设内容是否一致。", "依据", false),
        new("重复建设检查", "duplicate", "筛查可研自身重复功能点，并支持与历史批复功能点清单进行比对。", "重复", false),
        new("建设功能的对应关系检查", "function_correspondence", "检查建设内容、业务功能分析、系统功能需求分析、应用功能设计、功能点清单之间的对应关系。", "对应", true),
        new("数据填报合理性", "data_reasonableness", "检查用户量、业务量、并发数、高峰期用户、活跃用户比例、数据量、存储量和计算资源依据。", "数据", false),
        new("资源申请的合理性", "resource", "检查云资源和三大件资源申请是否与系统功能、数据规模和性能需求匹配。", "资源", false),
        new("安全内容的合理性", "security", "检查安全需求分析与项目建设内容、安全建设内容之间的匹配性。", "安全", false),
        new("价格合理性", "price", "根据软件造价标准拆解功能点，形成可复核的核价结果入口。", "造价", false),
        new("软硬件产品价格参考", "price_reference", "展示政采网、住建部询价网和价格数据的参考价信息。", "询价", false),
        new("敏感词检测", "sensitive_word", "识别报告中不符合要求的词语、表达等敏感或违规内容。", "敏感", true)
    };

    private static readonly Dictionary<string, List<RuleDisplayItem>> BuiltInRuleModules = new()
    {
        ["basis"] = new List<RuleDisplayItem>
        {
            new(
                "BASIS_007",
                "投资总额一致性校验规则",
                "一致性校验规则",
                "全文项目总投资应保持一致",
                "local_builtin"),
            new(
                "BASIS_009",
                "指标一致性校验规则",
                "一致性校验规则",
                "本项目验收的指标应与规划指标相一致",
                "local_builtin"),
            new(
                "BASIS_012",
                "功能模块一致性校验规则",
                "一致性校验规则",
                "功能模块需与预算投资表中的内容做到有效对应",
                "local_builtin"),
            new(
                "BASIS_014",
                "安全内容一致性校验规则",
                "一致性校验规则",
                "安全需求分析与安全建设内容应对应",
                "local_builtin")
        },
        ["function_correspondence"] = new List<RuleDisplayItem>
        {
            new(
                "FUNC_CORR_002",
                "建设依据上下文一致性校验规则",
                "一致性校验规则",
                "全文建设依据保持一致",
                "local_builtin"),
            new(
                "FUNC_CORR_006",
                "建设内容一致性校验规则",
                "一致性校验规则",
                "功能点需在需求描述章节、建设内容章节及投资概算章节中，形成一一对应的关联关系",
                "local_builtin")
        },
        ["data_reasonableness"] = new List<RuleDisplayItem>
        {
            new(
                "DATA_REASON_001",
                "项目预算一致性校验规则",
                "一致性校验规则",
                "项目预算数据计算无误",
                "local_builtin"),
            new(
                "DATA_REASON_002",
                "项目成效考核目标数量设置合规性审查规则",
                "内容合规性审查规则",
                "指标设置数量应满足要求",
                "local_builtin"),
            new(
                "DATA_REASON_003",
                "项目成效考核目标与建设内容的匹配性审查规则",
                "内容合规性审查规则",
                "项目成效考核目标设置具有合理性，参照《市级数字化项目规划指标参数填报操作手册（试行）》开展规划指标参数的拟定。",
                "local_builtin"),
            new(
                "DATA_REASON_004",
                "数据上链内容合规性审查规则",
                "内容合规性审查规则",
                "按照“新建即上链”的工作原则，明确对接政务目录链的数据上链内容，主要建设内容所产生的重点数据应实现应上尽上。",
                "local_builtin")
        },
        ["resource"] = new List<RuleDisplayItem>
        {
            new(
                "RESOURCE_REASON_001",
                "安全服务需求表、PaaS服务清单、密码服务资源内容清单的关联内容一致性校验规则",
                "一致性校验规则",
                "安全服务需求表、PaaS服务清单、密码服务资源内容清单中的关联内容数据应一致",
                "local_builtin"),
            new(
                "RESOURCE_REASON_002",
                "三大件数量一致性校验规则",
                "一致性校验规则",
                "每新申请一台服务器需配一套操作系统；每申请一台数据库服务器需配一个数据库",
                "local_builtin")
        },
        ["price"] = new List<RuleDisplayItem>
        {
            new(
                "PRICE_REASON_001",
                "二类费用计量计价规则",
                "计量计价规则",
                "依据《上海市市级数字化项目配置标准（二类费用）》核算咨询、监理、软件测试、系统集成、安全/等保及密码测评费用，并校验五项费用合计不超过项目总投资12%。适用范围：上海市市级项目。",
                "local_builtin")
        },
        ["price_reference"] = new List<RuleDisplayItem>
        {
            new(
                "PRICE_REF_001",
                "应用软件开发投资估算明细规则",
                "计量计价规则",
                "功能人月的单价为2.5万元；针对数字化新技术应用可调整为3万元；单个功能点的工作量上限为5个人月，下限为1个人月。适用范围：市级项目。判断条件：软件开发投资估算明细表填写是否合规。",
                "local_builtin"),
            new(
                "PRICE_REF_002",
                "软硬件产品计量计价规则",
                "计量计价规则",
                "应符合上海市产品库价格；应符合区级产品库价格。适用范围：市级项目、区级项目。判断条件：二类费用价格是否符合产品库价格，如产品库中无价格参考，需符合政府采购价格。",
                "local_builtin")
        },
        ["security"] = new List<RuleDisplayItem>
        {
            new(
                "SECURITY_REASON_001",
                "安全需求分析合规性审查规则",
                "内容合规性审查规则",
                "适用范围：市级项目。分析系统的安全风险，对信息系统安全等级给予准确定位，描述系统关于数据的相关安全要求，包括数据分类分级和所需的安全防护措施及密码应用措施。判断条件：4.7安全需求分析是否满足要求。",
                "local_builtin")
        }
    };

    public MainWindow()
    {
        InitializeComponent();
        ProjectComboBox.SelectionChanged += OnProjectSelectionChanged;
        PrepareText();
        PrepareNavigation();
        BuildCheckItems();
        Loaded += OnWindowLoaded;
    }

    private sealed record CheckItem(string Title, string ModuleCode, string Description, string Badge, bool IsChecked);
    private sealed record RuleDisplayItem(string RuleId, string RuleName, string RuleCategory, string RuleDetail, string Source);
    private sealed record ProjectOption(string Id, string Name);
    private sealed record RunResultInfo(string Id, string Module, string ModuleName, string Status, string Risk, int FindingsCount, string CreatedAt);

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
        InsertCompletenessPrecheck();
        SplitReportCheckPages();

        _recordsPage = BuildRecordsPage();
        _rulesPage = BuildRulesPage();
        _settingsPage = ScrollablePage(BuildSettingsPage(), showScrollBar: true);

        _reportNavButton = FindNavButton("完整性检查", "Report");
        _specialistNavButton = FindNavButton("可研报告专项检查", "Specialist");
        _recordsNavButton = FindNavButton("审查记录", "Records");
        _rulesNavButton = FindNavButton("规则库配置", "Rules");
        _settingsNavButton = FindNavButton("系统设置", "Settings");

        if (_rulesNavButton != null)
        {
            _rulesNavButton.Content = "规则库配置";
        }

        foreach (var button in new[] { _reportNavButton, _specialistNavButton, _recordsNavButton, _rulesNavButton, _settingsNavButton })
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
        var page = tag switch
        {
            "Specialist" => _specialistPage!,
            "Records" => _recordsPage!,
            "Rules" => _rulesPage!,
            "Settings" => _settingsPage!,
            _ => _reportPage
        };
        if (page == _reportPage || page == _specialistPage)
        {
            // Both pages use the same selection controls/state. Reparenting
            // preserves project events, file choice and in-flight task state.
            var target = (Grid)page;
            foreach (var (card, row) in new[] { (_projectSelectionCard, 1), (_reportSelectionCard, 2) })
            {
                if (card == null) continue;
                DetachFromParent(card);
                Grid.SetRow(card, row);
                target.Children.Add(card);
            }
        }
        DetachFromParent(page);
        _contentHost.Children.Add(page == _reportPage || page == _specialistPage ? ScrollablePage(page) : page);

        foreach (var button in new[] { _reportNavButton, _specialistNavButton, _recordsNavButton, _rulesNavButton, _settingsNavButton })
        {
            if (button != null)
            {
                button.Style = (Style)FindResource(button.Tag?.ToString() == tag ? "ActiveNavButton" : "NavButton");
            }
        }

        if (tag == "Records")
        {
            _ = LoadRecordsAsync();
        }
        else if (tag == "Rules")
        {
            _ = LoadRulesAsync();
        }
        else if (tag == "Settings")
        {
            _ = RefreshPriceBenchmarkStatusAsync();
        }
    }

    private static ScrollViewer ScrollablePage(UIElement page, bool showScrollBar = false)
    {
        return new ScrollViewer
        {
            VerticalScrollBarVisibility = showScrollBar ? ScrollBarVisibility.Visible : ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
            PanningMode = PanningMode.VerticalOnly,
            Content = page
        };
    }

    private static void DetachFromParent(UIElement page)
    {
        if (page is not FrameworkElement element || element.Parent is null)
        {
            return;
        }

        switch (element.Parent)
        {
            case Panel panel:
                panel.Children.Remove(page);
                break;
            case ContentControl contentControl when ReferenceEquals(contentControl.Content, page):
                contentControl.Content = null;
                break;
            case Decorator decorator when ReferenceEquals(decorator.Child, page):
                decorator.Child = null;
                break;
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
            if (row >= 4)
            {
                Grid.SetRow(child, row + 1);
            }
        }

        _reportPage.RowDefinitions.Insert(4, new RowDefinition { Height = GridLength.Auto });
        var section = BuildCompletenessPrecheck();
        Grid.SetRow(section, 4);
        _reportPage.Children.Add(section);
    }

    private Border BuildCompletenessPrecheck() => BuildRealCompletenessPrecheck();

    private void SplitReportCheckPages()
    {
        if (_reportPage == null) return;
        var children = _reportPage.Children.Cast<UIElement>().ToArray();
        _projectSelectionCard = children.Single(item => Grid.GetRow(item) == 2);
        _reportSelectionCard = children.Single(item => Grid.GetRow(item) == 3);
        var completeness = children.Single(item => Grid.GetRow(item) == 4);
        var specialistContent = children.Where(item => Grid.GetRow(item) >= 5).OrderBy(Grid.GetRow).ToArray();
        foreach (var item in specialistContent) _reportPage.Children.Remove(item);
        Grid.SetRow(completeness, 3);
        _reportPage.RowDefinitions.Clear();
        for (var i = 0; i < 4; i++) _reportPage.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        _specialistPage = new Grid();
        for (var i = 0; i < 4 + specialistContent.Length; i++)
            _specialistPage.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var heading = new StackPanel();
        heading.Children.Add(Text("可研报告专项检查", 28, FontWeights.Bold, FindBrush("TextBrush")));
        heading.Children.Add(Text("选择项目、报告和专项检测范围，创建任务并查看进度。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 6, 0, 0), true));
        _specialistPage.Children.Add(heading);
        _llmReviewCheckBox = new CheckBox { Content = "专项检测启用大模型辅助复核", IsChecked = false, Margin = new Thickness(0, 16, 0, 0) };
        Grid.SetRow(_llmReviewCheckBox, 3);
        _specialistPage.Children.Add(_llmReviewCheckBox);
        for (var i = 0; i < specialistContent.Length; i++)
        {
            Grid.SetRow(specialistContent[i], i + 4);
            _specialistPage.Children.Add(specialistContent[i]);
    }
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
                IsChecked = item.IsChecked,
                Tag = item,
                FontWeight = FontWeights.SemiBold,
                FontSize = 14,
                VerticalAlignment = VerticalAlignment.Center
            };
            checkBox.Checked += OnCheckItemSelectionChanged;
            checkBox.Unchecked += OnCheckItemSelectionChanged;
            header.Children.Add(checkBox);
            _checkBoxes.Add(checkBox);

            stack.Children.Add(header);
            stack.Children.Add(Text(item.Description, 14, null, Brush(102, 112, 133), new Thickness(0, 12, 0, 0), true));
            border.Child = stack;
            CheckItemsGrid.Children.Add(border);
        }
    }

    private async void OnWindowLoaded(object sender, RoutedEventArgs e)
    {
        await LoadProjectsAsync();
        await LoadRulesAsync();
    }

    private async Task LoadProjectsAsync()
    {
        try
        {
            using var response = await BackendClient.GetAsync($"{BackendBaseUrl}/projects");
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                StatusTextBlock.Text = $"项目加载失败：{ExtractErrorMessage(responseBody)}";
                return;
            }

            using var document = JsonDocument.Parse(responseBody);
            var projects = document.RootElement.ValueKind == JsonValueKind.Array
                ? document.RootElement.EnumerateArray()
                    .Select(item => new ProjectOption(
                        GetString(item, "id"),
                        FirstNonEmpty(GetString(item, "name"), "未命名项目")))
                    .Where(item => !string.IsNullOrWhiteSpace(item.Id))
                    .ToList()
                : new List<ProjectOption>();

            ProjectComboBox.ItemsSource = projects;
            var selected = projects.FirstOrDefault(item => item.Id == _selectedProjectId) ?? projects.FirstOrDefault();
            if (selected != null)
            {
                ProjectComboBox.SelectedItem = selected;
                _selectedProjectId = selected.Id;
                _selectedProjectName = selected.Name;
            }
            else
            {
                _selectedProjectId = null;
                _selectedProjectName = null;
            }
            UpdateDeleteSelectionButtonState();
        }
        catch (Exception exc)
        {
            StatusTextBlock.Text = $"项目加载失败：{exc.Message}";
        }
    }

    private async void OnProjectSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        ResetCompletenessResult();
        if (ProjectComboBox.SelectedItem is not ProjectOption project)
        {
            _selectedProjectId = null;
            _selectedProjectName = null;
            _selectedCheckRunId = null;
            UpdateDeleteSelectionButtonState();
            await LoadCompletenessHistoryAsync();
            return;
        }

        _selectedProjectId = project.Id;
        _selectedProjectName = project.Name;
        _selectedCheckRunId = null;
        UpdateDeleteSelectionButtonState();
        await LoadCompletenessHistoryAsync();
        StatusTextBlock.Text = $"已选择项目：{project.Name}。请选择报告并开始检测。";
    }

    private async void OnCreateProjectClicked(object sender, RoutedEventArgs e)
    {
        var dialog = BuildProjectDialog();
        if (dialog.ShowDialog() != true)
        {
            return;
        }

        var name = dialog.Tag as string;
        if (string.IsNullOrWhiteSpace(name))
        {
            return;
        }

        try
        {
            var payload = dialog.Content is FrameworkElement content && content.Tag is ProjectDialogValues values
                ? values
                : new ProjectDialogValues(name, string.Empty, string.Empty);
            using var request = new StringContent(
                JsonSerializer.Serialize(new
                {
                    name = payload.Name,
                    department = payload.Department,
                    description = payload.Description,
                }),
                Encoding.UTF8,
                "application/json");
            using var response = await BackendClient.PostAsync($"{BackendBaseUrl}/projects", request);
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                MessageBox.Show(this, $"创建项目失败：{ExtractErrorMessage(responseBody)}", "创建项目失败", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            using var document = JsonDocument.Parse(responseBody);
            _selectedProjectId = GetString(document.RootElement, "id");
            _selectedProjectName = FirstNonEmpty(GetString(document.RootElement, "name"), payload.Name);
            await LoadProjectsAsync();
            StatusTextBlock.Text = $"项目“{_selectedProjectName}”已创建并选中。";
        }
        catch (Exception exc)
        {
            MessageBox.Show(this, $"创建项目失败：{exc.Message}", "创建项目失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private Window BuildProjectDialog()
    {
        Window dialog = null!;
        var nameBox = new TextBox { Margin = new Thickness(0, 6, 0, 12), Padding = new Thickness(8, 6, 8, 6) };
        var departmentBox = new TextBox { Margin = new Thickness(0, 6, 0, 12), Padding = new Thickness(8, 6, 8, 6) };
        var descriptionBox = new TextBox
        {
            Margin = new Thickness(0, 6, 0, 16),
            Padding = new Thickness(8, 6, 8, 6),
            Height = 72,
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        };
        var panel = new StackPanel { Margin = new Thickness(22) };
        panel.Children.Add(Text("新建项目", 20, FontWeights.Bold, FindBrush("TextBrush")));
        panel.Children.Add(Text("项目用于归档同一项目下的多次报告检查。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 6, 0, 14), true));
        panel.Children.Add(Text("项目名称", 13, FontWeights.SemiBold, FindBrush("TextBrush")));
        panel.Children.Add(nameBox);
        panel.Children.Add(Text("所属部门（可选）", 13, FontWeights.SemiBold, FindBrush("TextBrush")));
        panel.Children.Add(departmentBox);
        panel.Children.Add(Text("项目说明（可选）", 13, FontWeights.SemiBold, FindBrush("TextBrush")));
        panel.Children.Add(descriptionBox);
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var cancelButton = Button("取消", false);
        cancelButton.Click += (_, _) => dialog.Close();
        var confirmButton = Button("创建项目", true);
        confirmButton.Click += (_, _) =>
        {
            if (string.IsNullOrWhiteSpace(nameBox.Text))
            {
                MessageBox.Show(dialog, "请输入项目名称。", "项目名称不能为空", MessageBoxButton.OK, MessageBoxImage.Warning);
                nameBox.Focus();
                return;
            }

            panel.Tag = new ProjectDialogValues(nameBox.Text.Trim(), departmentBox.Text.Trim(), descriptionBox.Text.Trim());
            dialog.Tag = nameBox.Text.Trim();
            dialog.DialogResult = true;
        };
        buttons.Children.Add(cancelButton);
        buttons.Children.Add(confirmButton);
        panel.Children.Add(buttons);

        dialog = new Window
        {
            Title = "新建项目",
            Owner = this,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            SizeToContent = SizeToContent.WidthAndHeight,
            Width = 440,
            ResizeMode = ResizeMode.NoResize,
            Content = panel,
            Background = Brushes.White,
        };
        return dialog;
    }

    private sealed record ProjectDialogValues(string Name, string Department, string Description);

    private async Task DeleteProjectAsync(string projectId, string projectName)
    {
        if (string.IsNullOrWhiteSpace(projectId))
        {
            return;
        }

        var confirm = MessageBox.Show(
            this,
            $"确定删除项目“{projectName}”吗？项目下的报告检查记录和检查结果也会被删除。",
            "删除项目",
            MessageBoxButton.OKCancel,
            MessageBoxImage.Warning);
        if (confirm != MessageBoxResult.OK)
        {
            return;
        }

        try
        {
            using var response = await BackendClient.DeleteAsync($"{BackendBaseUrl}/projects/{Uri.EscapeDataString(projectId)}");
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                MessageBox.Show(this, $"删除项目失败：{ExtractErrorMessage(responseBody)}", "删除项目失败", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (_selectedProjectId == projectId)
            {
                _selectedProjectId = null;
                _selectedProjectName = null;
                _selectedCheckRunId = null;
                _activeRecordId = null;
                _runResults.Clear();
                ResetRecordDetail("请选择一个项目和报告检查查看结果。");
                ResetDocumentPreview("选择检查项后加载完整原文。");
            }

            UpdateDeleteSelectionButtonState();
            await LoadProjectsAsync();
            await LoadRecordsAsync();
            StatusTextBlock.Text = $"项目“{projectName}”已删除。";
        }
        catch (Exception exc)
        {
            MessageBox.Show(this, $"删除项目失败：{exc.Message}", "删除项目失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async Task<string> CreateCheckRunAsync(string projectId, string reportPath)
    {
        var payload = JsonSerializer.Serialize(new
        {
            report_name = Path.GetFileNameWithoutExtension(reportPath),
            source_filename = Path.GetFileName(reportPath),
        });
        using var request = new StringContent(payload, Encoding.UTF8, "application/json");
        using var response = await BackendClient.PostAsync($"{BackendBaseUrl}/projects/{Uri.EscapeDataString(projectId)}/check-runs", request);
        var responseBody = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException($"创建报告检查记录失败：{ExtractErrorMessage(responseBody)}");
        }

        using var document = JsonDocument.Parse(responseBody);
        var checkRunId = GetString(document.RootElement, "id");
        if (string.IsNullOrWhiteSpace(checkRunId))
        {
            throw new InvalidOperationException("后端未返回报告检查记录编号。");
        }

        return checkRunId;
    }

    private async void OnCheckItemSelectionChanged(object sender, RoutedEventArgs e)
    {
        if (sender is not CheckBox { Tag: CheckItem item } checkBox)
        {
            return;
        }

        if (item.ModuleCode == "duplicate")
        {
            HandleDuplicateSelectionChanged(checkBox);
        }

        if (HasBackendRuleApi(item.ModuleCode))
        {
            await LoadRulesForModuleAsync(item.ModuleCode);
        }
    }

    private void HandleDuplicateSelectionChanged(CheckBox checkBox)
    {
        if (_isUpdatingDuplicateSelection)
        {
            return;
        }

        if (checkBox.IsChecked != true)
        {
            _duplicateHistoryFiles = new List<string>();
            _duplicateHistoryChoiceCaptured = false;
            return;
        }

        var historyFiles = AskDuplicateHistoryFiles();
        if (historyFiles == null)
        {
            _duplicateHistoryFiles = new List<string>();
            _duplicateHistoryChoiceCaptured = false;
            _isUpdatingDuplicateSelection = true;
            checkBox.IsChecked = false;
            _isUpdatingDuplicateSelection = false;
            StatusTextBlock.Text = "已取消重复建设检查。";
            return;
        }

        _duplicateHistoryFiles = historyFiles;
        _duplicateHistoryChoiceCaptured = true;
        StatusTextBlock.Text = historyFiles.Count > 0
            ? $"重复建设检查已选择 {historyFiles.Count} 个往期文件，开始检测时将执行跨报告比对。"
            : "重复建设检查将仅执行当前报告内部重复功能点检查。";
    }

    private Grid BuildRecordsPage()
    {
        var page = PageGrid(3);
        page.RowDefinitions[2].Height = new GridLength(1, GridUnitType.Star);
        var header = Header("审查记录", "查看报告审查任务、执行状态和结果摘要。", "刷新记录", false);
        if (header.Children.OfType<Button>().FirstOrDefault() is Button refreshButton)
        {
            refreshButton.Click += async (_, _) => await LoadRecordsAsync();
        }
        page.Children.Add(header);

        var stats = new UniformGrid { Columns = 3, Margin = new Thickness(0, 18, 0, 0) };
        Grid.SetRow(stats, 1);
        stats.Children.Add(StatCard("实时", "全部记录", "来自后端 CheckResult", Brush(37, 99, 235)));
        stats.Children.Add(StatCard("真实", "结果摘要", "展示最近 50 条", Brush(2, 122, 72)));
        stats.Children.Add(StatCard("可点选", "问题详情", "查看 findings 明细", Brush(181, 71, 8)));
        page.Children.Add(stats);

        var body = new Grid { Margin = new Thickness(0, 18, 0, 0) };
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(0.9, GridUnitType.Star), MinWidth = 210 });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.25, GridUnitType.Star), MinWidth = 300 });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.55, GridUnitType.Star), MinWidth = 350 });
        Grid.SetRow(body, 2);

        var projectPanel = new StackPanel();
        _projectTreePanel = projectPanel;
        var projectHeader = new Grid { Margin = new Thickness(0, 0, 0, 10) };
        projectHeader.ColumnDefinitions.Add(new ColumnDefinition());
        projectHeader.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        projectHeader.Children.Add(Text("项目", 18, FontWeights.Bold, FindBrush("TextBrush")));
        var createProjectButton = Button("新建项目", false);
        createProjectButton.Padding = new Thickness(10, 7, 10, 7);
        createProjectButton.Click += OnCreateProjectClicked;
        Grid.SetColumn(createProjectButton, 1);
        projectHeader.Children.Add(createProjectButton);
        var deleteSelectionButton = Button("删除", false);
        deleteSelectionButton.Padding = new Thickness(10, 7, 10, 7);
        deleteSelectionButton.Margin = new Thickness(8, 0, 0, 0);
        deleteSelectionButton.ToolTip = "删除当前选中的项目或检查记录";
        deleteSelectionButton.IsEnabled = false;
        deleteSelectionButton.Click += async (_, _) => await DeleteSelectedProjectOrRunAsync();
        Grid.SetColumn(deleteSelectionButton, 2);
        projectHeader.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        projectHeader.Children.Add(deleteSelectionButton);
        _deleteSelectionButton = deleteSelectionButton;
        projectPanel.Children.Add(projectHeader);
        projectPanel.Children.Add(Text("依次展开项目、报告和检查项。", 12, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 12), true));
        projectPanel.Children.Add(Text("正在加载项目...", 13, null, FindBrush("MutedBrush"), null, true));
        var projectScroller = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = projectPanel };
        body.Children.Add(Card(projectScroller, new Thickness(14), new Thickness(0, 0, 14, 0)));

        _recordDetailPanel = new StackPanel();
        ResetRecordDetail();
        var detailScroller = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Content = _recordDetailPanel };
        var detailCard = Card(detailScroller, new Thickness(16), new Thickness(0, 0, 14, 0));
        Grid.SetColumn(detailCard, 1);
        body.Children.Add(detailCard);

        var previewCard = Card(BuildDocumentPreviewPane(), new Thickness(0), new Thickness(0));
        Grid.SetColumn(previewCard, 2);
        body.Children.Add(previewCard);

        page.Children.Add(body);
        return page;
    }

    private Grid BuildDocumentPreviewPane()
    {
        var grid = new Grid();
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

        var header = new Grid
        {
            Background = Brush(248, 250, 252),
            Margin = new Thickness(0)
        };
        header.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        header.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _documentTitleTextBlock = Text("报告原文", 16, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(14, 10, 14, 0), true);
        header.Children.Add(_documentTitleTextBlock);
        _documentLocationTextBlock = Text("选择左侧检查项后加载 Word 原文。", 12, null, FindBrush("MutedBrush"), new Thickness(14, 4, 14, 10), true);
        Grid.SetRow(_documentLocationTextBlock, 1);
        header.Children.Add(_documentLocationTextBlock);
        grid.Children.Add(header);

        _documentWebView = new WebView2
        {
            DefaultBackgroundColor = System.Drawing.Color.FromArgb(238, 242, 247),
            Margin = new Thickness(1, 0, 1, 1)
        };
        Grid.SetRow(_documentWebView, 1);
        grid.Children.Add(_documentWebView);
        _ = ShowPreviewPlaceholderAsync("选择一条审查结果后，可在这里查看并定位完整原文。");
        return grid;
    }

    private Grid BuildRulesPage()
    {
        var page = PageGrid(3);
        page.RowDefinitions[2].Height = new GridLength(1, GridUnitType.Star);
        page.Children.Add(Header("规则库配置", "治理审查规则、版本、适用范围与复核策略。", "新增规则", true));

        var stats = new UniformGrid { Columns = 4, Margin = new Thickness(0, 18, 0, 0) };
        Grid.SetRow(stats, 1);
        stats.Children.Add(StatCard((BuiltInRuleModules.Count + 1).ToString(), "规则模块", "规则库配置页当前展示", Brush(37, 99, 235)));
        stats.Children.Add(StatCard("全选", "默认策略", "勾选状态保存在本次会话", Brush(2, 122, 72)));
        stats.Children.Add(StatCard("API", "优先来源", "不可达时使用本地规则", Brush(181, 71, 8)));
        _ruleStatsTextBlock = Text("加载中", 30, FontWeights.Bold, Brush(52, 64, 84));
        var statStack = new StackPanel();
        statStack.Children.Add(_ruleStatsTextBlock);
        statStack.Children.Add(Text("当前规则", 17, FontWeights.SemiBold, FindBrush("TextBrush")));
        statStack.Children.Add(Text("按模块动态刷新", 14, null, FindBrush("MutedBrush"), new Thickness(0, 4, 0, 0)));
        stats.Children.Add(Card(statStack, new Thickness(18, 14, 18, 14), new Thickness(0, 0, 12, 0)));
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
        var header = Header("系统设置", "统一配置客户端运行、文件保存、审查偏好和服务连接。", "保存设置", true);
        if (header.Children.OfType<Button>().FirstOrDefault() is Button saveButton)
        {
            saveButton.Click += async (_, _) => await SaveLlmSettingsAsync();
        }
        page.Children.Add(header);

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
        var importPriceButton = Button("导入价格参考库", false);
        importPriceButton.Click += async (_, _) => await ImportPriceBenchmarksAsync();
        _priceBenchmarkStatusTextBlock = Text("正在读取价格参考库...", 12, null, FindBrush("MutedBrush"), new Thickness(0, 8, 0, 0), true);
        right.Children.Add(SettingsPanel("价格参考库", "维护软硬件产品的名称、品牌、型号、规格、单价和价格来源。", new UIElement[]
        {
            SettingRow("价格基准数据", "支持 Excel 或 CSV；相同来源、名称、品牌和型号会更新而不是重复新增", importPriceButton),
            _priceBenchmarkStatusTextBlock
        }));
        _llmApiKeyBox = new PasswordBox { Style = (Style)FindResource("PasswordInput"), ToolTip = "输入后保存，界面不会回显密钥" };
        _llmBaseUrlBox = Input(GetUserEnvironment("DEEPSEEK_API_BASE_URL", "https://llmapi.tongji.edu.cn/v1"));
        _llmModelBox = Input(GetUserEnvironment("DEEPSEEK_MODEL", "DeepSeek-R1"));
        _llmConfigStatusTextBlock = Text(
            HasUserApiKey() ? "当前用户密钥已配置（已隐藏）" : "当前用户尚未配置 API Key",
            12,
            null,
            FindBrush("MutedBrush"),
            new Thickness(0, 8, 0, 0),
            true);
        var testLlmButton = Button("测试连接", false);
        testLlmButton.Click += async (_, _) => await TestLlmConnectionAsync();
        right.Children.Add(SettingsPanel("大模型 API 配置", "配置 DeepSeek 或 OpenAI 兼容接口。密钥仅保存到当前 Windows 用户环境变量，不会在界面中显示。", new UIElement[]
        {
            SettingRow("API Key", "留空表示保留现有密钥；输入新值后点击顶部保存设置", _llmApiKeyBox),
            SettingRow("接口地址", "填写到 /v1，不要包含 /chat/completions", _llmBaseUrlBox),
            SettingRow("模型名称", "例如 DeepSeek-R1 或兼容接口提供的模型名", _llmModelBox),
            ActionRow(testLlmButton),
            _llmConfigStatusTextBlock
        }, new Thickness(0, 14, 0, 0)));
        right.Children.Add(SettingsPanel("服务连接", "配置客户端访问审查服务的地址和响应策略。", new UIElement[]
        {
            SettingRow("服务地址", "当前客户端请求入口", Input("http://127.0.0.1:8000/api")),
            SettingRow("请求超时", "大文件或复杂规则审查建议使用 60 秒以上", Combo("30 秒", "60 秒", "120 秒")),
            ActionRow(Button("测试连接", false))
        }, new Thickness(0, 14, 0, 0)));
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

    private static bool HasUserEnvironment(string name) =>
        !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable(name, EnvironmentVariableTarget.User));

    private static bool HasUserApiKey() =>
        HasUserEnvironment("DEEPSEEK_API_KEY") || HasUserEnvironment("LLM_API_KEY");

    private static string GetUserEnvironment(string name, string fallback)
    {
        return Environment.GetEnvironmentVariable(name, EnvironmentVariableTarget.User)?.Trim() is { Length: > 0 } value
            ? value
            : fallback;
    }

    private async Task SaveLlmSettingsAsync()
    {
        if (_llmApiKeyBox == null || _llmBaseUrlBox == null || _llmModelBox == null)
        {
            return;
        }

        var apiKey = _llmApiKeyBox.Password.Trim();
        var baseUrl = _llmBaseUrlBox.Text.Trim().TrimEnd('/');
        var model = _llmModelBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(baseUrl) || string.IsNullOrWhiteSpace(model))
        {
            MessageBox.Show(this, "接口地址和模型名称不能为空。", "保存失败", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        if (string.IsNullOrWhiteSpace(apiKey) && !HasUserApiKey())
        {
            MessageBox.Show(this, "请输入 API Key。", "保存失败", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        try
        {
            if (!string.IsNullOrWhiteSpace(apiKey))
            {
                SetUserEnvironment("DEEPSEEK_API_KEY", apiKey);
                SetUserEnvironment("LLM_API_KEY", apiKey);
            }
            else if (!HasUserEnvironment("DEEPSEEK_API_KEY"))
            {
                // Migrate an older generic-only configuration without exposing it in the UI.
                var existingGenericKey = GetUserEnvironment("LLM_API_KEY", string.Empty);
                if (!string.IsNullOrWhiteSpace(existingGenericKey))
                {
                    SetUserEnvironment("DEEPSEEK_API_KEY", existingGenericKey);
                }
            }

            SetUserEnvironment("DEEPSEEK_API_URL", baseUrl);
            SetUserEnvironment("DEEPSEEK_API_BASE_URL", baseUrl);
            SetUserEnvironment("DEEPSEEK_MODEL", model);
            SetUserEnvironment("LLM_BASE_URL", baseUrl);
            SetUserEnvironment("LLM_MODEL", model);
            _llmApiKeyBox.Clear();
            _llmConfigStatusTextBlock!.Text = "已保存到当前 Windows 用户环境变量；请重启后端后生效。";
            StatusTextBlock.Text = "大模型配置已保存，请重启后端。";
            MessageBox.Show(this, "大模型配置已保存。请重启后端，新的 API Key 和模型配置才会生效。", "保存成功", MessageBoxButton.OK, MessageBoxImage.Information);
        }
        catch (Exception exc)
        {
            _llmConfigStatusTextBlock!.Text = "配置保存失败。";
            MessageBox.Show(this, $"大模型配置保存失败：{exc.Message}", "保存失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }

        await Task.CompletedTask;
    }

    private static void SetUserEnvironment(string name, string value)
    {
        Environment.SetEnvironmentVariable(name, value, EnvironmentVariableTarget.User);
        Environment.SetEnvironmentVariable(name, value, EnvironmentVariableTarget.Process);
    }

    private async Task TestLlmConnectionAsync()
    {
        if (_llmConfigStatusTextBlock == null)
        {
            return;
        }

        _llmConfigStatusTextBlock.Text = "正在测试后端中的大模型配置...";
        try
        {
            using var statusResponse = await BackendClient.GetAsync($"{BackendBaseUrl}/evaluate/llm/status");
            var statusBody = await statusResponse.Content.ReadAsStringAsync();
            if (!statusResponse.IsSuccessStatusCode)
            {
                _llmConfigStatusTextBlock.Text = $"大模型状态读取失败：{ExtractErrorMessage(statusBody)}";
                return;
            }

            using var statusDocument = JsonDocument.Parse(statusBody);
            if (!GetBool(statusDocument.RootElement, "configured"))
            {
                _llmConfigStatusTextBlock.Text = "后端当前未读取到 API Key；请保存配置并重启后端。";
                return;
            }

            using var pingResponse = await BackendClient.PostAsync(
                $"{BackendBaseUrl}/evaluate/llm/ping",
                new StringContent("{\"text\":\"桌面端配置连通性测试\"}", Encoding.UTF8, "application/json"));
            var pingBody = await pingResponse.Content.ReadAsStringAsync();
            if (!pingResponse.IsSuccessStatusCode)
            {
                _llmConfigStatusTextBlock.Text = $"大模型连通性测试失败：{ExtractErrorMessage(pingBody)}";
                return;
            }

            using var pingDocument = JsonDocument.Parse(pingBody);
            _llmConfigStatusTextBlock.Text = GetBool(pingDocument.RootElement, "success")
                ? "大模型连接成功。"
                : $"大模型连接失败：{FirstNonEmpty(GetString(pingDocument.RootElement, "error"), "请检查 API Key、地址和模型。")}";
        }
        catch (Exception exc)
        {
            _llmConfigStatusTextBlock.Text = $"大模型连通性测试失败：{exc.Message}";
        }
    }

    private async Task RefreshPriceBenchmarkStatusAsync()
    {
        if (_priceBenchmarkStatusTextBlock == null)
        {
            return;
        }

        try
        {
            using var response = await BackendClient.GetAsync($"{BackendBaseUrl}/evaluate/price-benchmarks?limit=1000");
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                _priceBenchmarkStatusTextBlock.Text = $"价格参考库读取失败：{ExtractErrorMessage(responseBody)}";
                return;
            }

            using var document = JsonDocument.Parse(responseBody);
            _priceBenchmarkStatusTextBlock.Text = $"当前有效价格基准：{GetInt(document.RootElement, "count")} 条";
        }
        catch (Exception exc)
        {
            _priceBenchmarkStatusTextBlock.Text = $"价格参考库读取失败：{exc.Message}";
        }
    }

    private async Task ImportPriceBenchmarksAsync()
    {
        var dialog = new OpenFileDialog
        {
            Title = "导入软硬件价格参考库",
            Filter = "价格数据|*.xlsx;*.xlsm;*.csv|所有文件|*.*"
        };
        if (dialog.ShowDialog(this) != true)
        {
            return;
        }

        if (_priceBenchmarkStatusTextBlock != null)
        {
            _priceBenchmarkStatusTextBlock.Text = "正在导入价格参考库...";
        }

        try
        {
            await using var stream = File.OpenRead(dialog.FileName);
            using var form = new MultipartFormDataContent();
            using var fileContent = new StreamContent(stream);
            fileContent.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
            form.Add(fileContent, "file", Path.GetFileName(dialog.FileName));
            using var response = await BackendClient.PostAsync($"{BackendBaseUrl}/evaluate/price-benchmarks/import-file", form);
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                throw new InvalidOperationException(ExtractErrorMessage(responseBody));
            }

            using var document = JsonDocument.Parse(responseBody);
            var root = document.RootElement;
            var imported = GetInt(root, "imported");
            var updated = GetInt(root, "updated");
            var skipped = GetInt(root, "skipped");
            _priceBenchmarkStatusTextBlock!.Text = $"导入完成：新增 {imported} 条，更新 {updated} 条，跳过 {skipped} 条。";
        }
        catch (Exception exc)
        {
            if (_priceBenchmarkStatusTextBlock != null)
            {
                _priceBenchmarkStatusTextBlock.Text = $"导入失败：{exc.Message}";
            }
        }
    }
    private Border BuildRuleCategories()
    {
        var stack = new StackPanel();
        _rulesDirectoryPanel = stack;
        stack.Children.Add(Text("规则目录", 18, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(0, 0, 0, 6)));
        stack.Children.Add(Text("按审查场景组织，快速定位规则集。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 14), true));
        stack.Children.Add(Text("正在读取规则库...", 13, null, FindBrush("MutedBrush"), null, true));
        return Card(stack, new Thickness(16), new Thickness(0, 0, 14, 0));
    }

    private Border BuildRuleEditor()
    {
        var stack = new StackPanel();
        _rulesEditorPanel = stack;
        stack.Children.Add(Text("规则详情", 18, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(0, 0, 0, 6)));
        stack.Children.Add(Text("选择左侧模块后展示真实规则，默认全选。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 14), true));
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
            Filter = "报告文件|*.docx;*.txt;*.xlsx;*.xlsm|所有文件|*.*"
        };

        if (dialog.ShowDialog(this) == true)
        {
            ReportPathTextBlock.Text = dialog.FileName;
            _completenessAttachments.Clear();
            UpdateCompletenessAttachmentLabel();
            ResetCompletenessResult();
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

    private async void OnStartCheckClicked(object sender, RoutedEventArgs e)
    {
        if (_isChecking)
        {
            return;
        }

        if (ReportPathTextBlock.Text == "尚未选择可研报告")
        {
            MessageBox.Show(this, "请先选择可研报告文件。", "缺少报告", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        if (string.IsNullOrWhiteSpace(_selectedProjectId))
        {
            MessageBox.Show(this, "请先选择所属项目，或新建一个项目。", "缺少项目", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        var reportPath = ReportPathTextBlock.Text;
        if (!File.Exists(reportPath))
        {
            MessageBox.Show(this, "选择的报告文件不存在，请重新选择。", "文件不存在", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        var selectedItems = _checkBoxes
            .Where(item => item.IsChecked == true)
            .Select(item => item.Tag as CheckItem)
            .Where(item => item != null)
            .Cast<CheckItem>()
            .ToList();
        if (selectedItems.Count == 0)
        {
            MessageBox.Show(this, "请至少选择一个检测项。", "缺少检测项", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        var runnableItems = selectedItems.Where(item => IsRunnableModule(item.ModuleCode)).ToList();
        if (runnableItems.Count == 0)
        {
            MessageBox.Show(this, "请选择已接入后端的检测项后重试。", "暂无可执行检测项", MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }

        if (!_isCompletenessChecked)
        {
            StatusTextBlock.Text = "尚未执行前置完整性检查，本次继续执行已接入的专项检测。";
        }

        var startButton = sender as Button;
        try
        {
            _isChecking = true;
            if (startButton != null)
            {
                startButton.IsEnabled = false;
            }

            ResetResults();
            StatusTextBlock.Text = "正在检查后端服务连接...";
            if (!await IsBackendAvailableAsync())
            {
                StatusTextBlock.Text = "后端服务未启动。";
                MessageBox.Show(this, "无法连接后端服务，请先启动目标项目后端：run_backend.bat（端口 8000）", "后端未启动", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            var skippedCount = selectedItems.Count - runnableItems.Count;
            if (skippedCount > 0)
            {
                AddResultNotice($"已跳过 {skippedCount} 个尚未接入后端的检测项。");
            }

            var duplicateHistoryFiles = ResolveDuplicateHistoryFiles(runnableItems);
            if (duplicateHistoryFiles == null)
            {
                StatusTextBlock.Text = "已取消检测。";
                return;
            }

            var projectId = _selectedProjectId;
            if (string.IsNullOrWhiteSpace(projectId))
            {
                throw new InvalidOperationException("未选择项目。");
            }

            _selectedCheckRunId = await CreateCheckRunAsync(projectId, reportPath);

            var taskItems = runnableItems.ToList();
            var duplicateItem = taskItems.FirstOrDefault(item => item.ModuleCode == "duplicate");
            if (duplicateItem != null && duplicateHistoryFiles.Count > 0)
            {
                taskItems.Remove(duplicateItem);
            }

            foreach (var item in taskItems)
            {
                await LoadRulesForModuleAsync(item.ModuleCode);
            }

            if (duplicateItem != null && duplicateHistoryFiles.Count > 0)
            {
                StatusTextBlock.Text = "正在执行重复建设跨报告比对...";
                try
                {
                    var duplicateResult = await UploadAndRunDuplicateCompareAsync(reportPath, duplicateHistoryFiles, projectId, _selectedCheckRunId);
                    RenderCheckResult(duplicateResult);
                    AddResultNotice("重复建设检查已完成：已完成当前报告内部和往期报告跨报告比对。");
                    await LoadRecordsAsync();
                }
                catch (Exception exc)
                {
                    // Cross-report comparison can fail on a malformed or very
                    // large history attachment. Keep this batch's duplicate
                    // check by falling back to the internal comparison path.
                    AddResultNotice($"跨报告比对失败，已回退为当前报告内部重复检查：{exc.Message}");
                    StatusTextBlock.Text = "跨报告比对失败，正在执行当前报告内部重复检查...";
                    var fallbackTaskId = await CreateEvaluateTaskAsync(
                        new[] { duplicateItem },
                        reportPath,
                        projectId,
                        _selectedCheckRunId);
                    await PollEvaluateTaskAsync(fallbackTaskId);
                }
            }

            if (taskItems.Count > 0)
            {
                StatusTextBlock.Text = "正在创建后台检测任务...";
                var taskId = await CreateEvaluateTaskAsync(taskItems, reportPath, projectId, _selectedCheckRunId);
                AddResultNotice("检测任务已创建，正在后台审查……");
                await PollEvaluateTaskAsync(taskId);
            }
            else if (duplicateItem != null && duplicateHistoryFiles.Count > 0)
            {
                StatusTextBlock.Text = "检测完成，请在“审查记录”查看结果。";
            }
        }
        catch (HttpRequestException exc)
        {
            StatusTextBlock.Text = "检测失败：后端请求异常。";
            AddResultNotice($"请求失败：{exc.Message}");
        }
        catch (TaskCanceledException)
        {
            StatusTextBlock.Text = "检测失败：请求超时。";
            AddResultNotice("请求超时，请确认后端服务运行正常，或稍后重试。");
        }
        catch (Exception exc)
        {
            StatusTextBlock.Text = "检测失败。";
            AddResultNotice(exc.Message);
        }
        finally
        {
            _isChecking = false;
            if (startButton != null)
            {
                startButton.IsEnabled = true;
            }
        }
    }

    private static bool IsRunnableModule(string moduleCode) =>
        moduleCode is "basis" or "duplicate" or "function_correspondence" or "data_reasonableness" or "security" or "resource" or "sensitive_word" or "price" or "price_reference";

    private static bool HasBackendRuleApi(string moduleCode) =>
        moduleCode is "basis" or "function_correspondence" or "data_reasonableness" or "security" or "resource" or "sensitive_word" or "price" or "price_reference";

    private static string EndpointForModule(string moduleCode) => moduleCode switch
    {
        "basis" => $"{BackendBaseUrl}/evaluate/basis/document",
        "duplicate" => $"{BackendBaseUrl}/evaluate/duplicate/internal",
        "function_correspondence" => $"{BackendBaseUrl}/evaluate/function-correspondence",
        "data_reasonableness" => $"{BackendBaseUrl}/evaluate/data-rules/document",
        "security" => $"{BackendBaseUrl}/evaluate/security/document",
        "resource" => $"{BackendBaseUrl}/evaluate/resource",
        "sensitive_word" => $"{BackendBaseUrl}/evaluate/sensitive-word",
        "price" => $"{BackendBaseUrl}/evaluate/price/document",
        "price_reference" => $"{BackendBaseUrl}/evaluate/price-reference/document",
        _ => throw new InvalidOperationException($"暂未接入检测模块：{moduleCode}")
    };

    private static string ProjectLevelForModule(string moduleCode) =>
        moduleCode is "function_correspondence" or "security" or "price" or "price_reference" ? "市级项目" : "通用";

    private List<string>? ResolveDuplicateHistoryFiles(IReadOnlyCollection<CheckItem> runnableItems)
    {
        if (!runnableItems.Any(item => item.ModuleCode == "duplicate"))
        {
            return new List<string>();
        }

        if (_duplicateHistoryChoiceCaptured)
        {
            return _duplicateHistoryFiles;
        }

        var historyFiles = AskDuplicateHistoryFiles();
        if (historyFiles != null)
        {
            _duplicateHistoryFiles = historyFiles;
            _duplicateHistoryChoiceCaptured = true;
        }

        return historyFiles;
    }

    private List<string>? AskDuplicateHistoryFiles()
    {
        var answer = MessageBox.Show(
            this,
            "本次重复建设检查是否有对应的往期可研报告、历史批复报告或历史功能点清单需要一起比对？",
            "重复建设检查",
            MessageBoxButton.YesNoCancel,
            MessageBoxImage.Question);

        if (answer == MessageBoxResult.Cancel)
        {
            return null;
        }

        if (answer == MessageBoxResult.No)
        {
            return new List<string>();
        }

        var dialog = new OpenFileDialog
        {
            Title = "选择往期可研报告或历史功能点清单",
            Filter = "可研报告/功能点清单|*.docx;*.xlsx;*.csv|Word 文档|*.docx|Excel/CSV 清单|*.xlsx;*.csv|所有文件|*.*",
            Multiselect = true
        };

        if (dialog.ShowDialog(this) != true || dialog.FileNames.Length == 0)
        {
            MessageBox.Show(this, "未选择往期文件，本次取消重复建设检查。", "缺少往期文件", MessageBoxButton.OK, MessageBoxImage.Information);
            return null;
        }

        return dialog.FileNames.Where(File.Exists).ToList();
    }

    private async Task<bool> IsBackendAvailableAsync()
    {
        try
        {
            using var response = await BackendClient.GetAsync($"{BackendBaseUrl}/health");
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private async Task LoadRulesAsync()
    {
        if (!_rulesByModule.ContainsKey("function_correspondence") || !_rulesByModule.ContainsKey("sensitive_word"))
        {
            RulesListPanel.Children.Clear();
            _ruleCheckBoxesByModule.Clear();
            _rulesByModule.Clear();
            AddBuiltInRuleModules();
            await LoadRulesForModuleAsync("function_correspondence");
            await LoadRulesForModuleAsync("sensitive_word");
        }
        else
        {
            AddBuiltInRuleModules();
        }

        RenderRuleDirectory();
        RenderRuleModule(_activeRuleModule);
    }

    private void AddBuiltInRuleModules()
    {
        foreach (var (moduleCode, rules) in BuiltInRuleModules)
        {
            if (!_rulesByModule.ContainsKey(moduleCode))
            {
                _rulesByModule[moduleCode] = new List<RuleDisplayItem>(rules);
            }
            else
            {
                MergeBuiltInRules(moduleCode);
            }
        }
    }

    private void MergeBuiltInRules(string moduleCode)
    {
        if (!BuiltInRuleModules.TryGetValue(moduleCode, out var builtInRules) || builtInRules.Count == 0)
        {
            return;
        }

        if (!_rulesByModule.TryGetValue(moduleCode, out var existingRules))
        {
            _rulesByModule[moduleCode] = new List<RuleDisplayItem>(builtInRules);
            return;
        }

        var existingIds = existingRules.Select(item => item.RuleId).ToHashSet();
        var existingNames = existingRules.Select(item => item.RuleName).ToHashSet();
        foreach (var rule in builtInRules)
        {
            if (existingIds.Contains(rule.RuleId) || existingNames.Contains(rule.RuleName))
            {
                continue;
            }

            existingRules.Add(rule);
            existingIds.Add(rule.RuleId);
            existingNames.Add(rule.RuleName);
        }
    }

    private async Task LoadRulesForModuleAsync(string moduleCode)
    {
        if (!HasBackendRuleApi(moduleCode))
        {
            return;
        }

        if (_ruleCheckBoxesByModule.TryGetValue(moduleCode, out var existing) && existing.Count > 0)
        {
            return;
        }

        try
        {
            using var response = await BackendClient.GetAsync($"{BackendBaseUrl}/evaluate/rules?module={moduleCode}");
            if (!response.IsSuccessStatusCode)
            {
                AddRuleLoadError(moduleCode, await response.Content.ReadAsStringAsync());
                return;
            }

            using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
            AddRuleSection(document.RootElement);
        }
        catch (Exception exc)
        {
            AddRuleLoadError(moduleCode, exc.Message);
        }
    }

    private void AddRuleLoadError(string moduleCode, string message)
    {
        var moduleName = ModuleDisplayName(moduleCode);
        _ruleCheckBoxesByModule[moduleCode] = new List<CheckBox>();
        _rulesByModule[moduleCode] = new List<RuleDisplayItem>();
        RulesListPanel.Children.Add(Card(
            Text($"{moduleName} 规则加载失败：{message}", 13, null, Brush(180, 35, 24), null, true),
            new Thickness(12),
            new Thickness(0, 0, 0, 10)));
    }

    private void AddRuleSection(JsonElement root)
    {
        var moduleCode = GetString(root, "module_code");
        var moduleName = GetString(root, "module_name");
        var source = GetString(root, "source");
        var checkBoxes = new List<CheckBox>();
        var ruleItems = new List<RuleDisplayItem>();
        _ruleCheckBoxesByModule[moduleCode] = checkBoxes;
        _rulesByModule[moduleCode] = ruleItems;

        var stack = new StackPanel();
        stack.Children.Add(Text(moduleName, 15, FontWeights.Bold, FindBrush("TextBrush")));
        stack.Children.Add(Text($"规则来源：{(source == "rule_api" ? "规则库 API" : "本地默认规则")}", 12, null, FindBrush("MutedBrush"), new Thickness(0, 3, 0, 8)));

        if (root.TryGetProperty("rules", out var rules) && rules.ValueKind == JsonValueKind.Array)
        {
            foreach (var rule in rules.EnumerateArray())
            {
                var ruleId = GetString(rule, "rule_id");
                var ruleName = GetString(rule, "rule_name");
                var category = GetString(rule, "rule_category");
                var detail = GetString(rule, "rule_detail");
                ruleItems.Add(new RuleDisplayItem(ruleId, ruleName, category, detail, source));
                var checkBox = new CheckBox
                {
                    Content = RuleDisplayContent(moduleCode, ruleName, category),
                    IsChecked = true,
                    Tag = ruleId,
                    Margin = new Thickness(0, 0, 0, 4),
                    FontWeight = FontWeights.SemiBold
                };
                checkBoxes.Add(checkBox);
                stack.Children.Add(checkBox);
                if (!string.IsNullOrWhiteSpace(detail))
                {
                    stack.Children.Add(Text(detail, 12, null, FindBrush("MutedBrush"), new Thickness(22, 0, 0, 8), true));
                }
            }
        }

        AddBuiltInRulesToSection(moduleCode, ruleItems, checkBoxes, stack);

        if (checkBoxes.Count == 0)
        {
            stack.Children.Add(Text("未读取到规则，后端会使用检查模块内置默认逻辑。", 12, null, Brush(181, 71, 8), null, true));
        }

        RulesListPanel.Children.Add(Card(stack, new Thickness(12), new Thickness(0, 0, 0, 10)));
    }

    private void AddBuiltInRulesToSection(string moduleCode, List<RuleDisplayItem> ruleItems, List<CheckBox> checkBoxes, StackPanel stack)
    {
        if (!BuiltInRuleModules.TryGetValue(moduleCode, out var builtInRules) || builtInRules.Count == 0)
        {
            return;
        }

        var existingIds = ruleItems.Select(item => item.RuleId).ToHashSet();
        var existingNames = ruleItems.Select(item => item.RuleName).ToHashSet();
        foreach (var rule in builtInRules)
        {
            if (existingIds.Contains(rule.RuleId) || existingNames.Contains(rule.RuleName))
            {
                continue;
            }

            ruleItems.Add(rule);
            existingIds.Add(rule.RuleId);
            existingNames.Add(rule.RuleName);

            var checkBox = new CheckBox
            {
                Content = RuleDisplayContent(moduleCode, rule.RuleName, rule.RuleCategory),
                IsChecked = true,
                Tag = rule.RuleId,
                Margin = new Thickness(0, 0, 0, 4),
                FontWeight = FontWeights.SemiBold
            };
            checkBoxes.Add(checkBox);
            stack.Children.Add(checkBox);
            if (!string.IsNullOrWhiteSpace(rule.RuleDetail))
            {
                stack.Children.Add(Text(rule.RuleDetail, 12, null, FindBrush("MutedBrush"), new Thickness(22, 0, 0, 8), true));
            }
        }
    }

    private async Task LoadRecordsAsync()
    {
        if (_projectTreePanel == null)
        {
            return;
        }

        var header = _projectTreePanel.Children.OfType<Grid>().FirstOrDefault();
        _projectTreePanel.Children.Clear();
        UpdateDeleteSelectionButtonState();
        if (header != null)
        {
            _projectTreePanel.Children.Add(header);
        }
        _projectTreePanel.Children.Add(Text("依次展开项目、报告和检查项。", 12, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 12), true));
        _projectTreePanel.Children.Add(Text("正在加载项目...", 13, null, FindBrush("MutedBrush"), null, true));

        try
        {
            using var response = await BackendClient.GetAsync($"{BackendBaseUrl}/projects/tree");
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                _projectTreePanel.Children.Clear();
                _projectTreePanel.Children.Add(Text($"加载失败：{ExtractErrorMessage(responseBody)}", 13, null, Brush(180, 35, 24), null, true));
                return;
            }

            using var document = JsonDocument.Parse(responseBody);
            _projectTreeRoot = document.RootElement.Clone();
            RenderProjectTree(document.RootElement);
        }
        catch (Exception exc)
        {
            _projectTreePanel.Children.Clear();
            _projectTreePanel.Children.Add(Text($"加载失败：{exc.Message}", 13, null, Brush(180, 35, 24), null, true));
        }
    }

    private void RenderProjectTree(JsonElement root)
    {
        if (_projectTreePanel == null)
        {
            return;
        }

        var header = _projectTreePanel.Children.OfType<Grid>().FirstOrDefault();
        _projectTreePanel.Children.Clear();
        if (header != null)
        {
            _projectTreePanel.Children.Add(header);
        }
        _projectTreePanel.Children.Add(Text("依次展开项目、报告和检查项。", 12, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 12), true));

        if (!root.TryGetProperty("items", out var items) || items.ValueKind != JsonValueKind.Array || items.GetArrayLength() == 0)
        {
            _projectTreePanel.Children.Add(Text("暂无项目。请先创建项目并开始一次检测。", 13, null, FindBrush("MutedBrush"), null, true));
            ResetRecordDetail();
            return;
        }

        foreach (var item in items.EnumerateArray())
        {
            RenderProjectNode(item);
        }
    }

    private void RenderProjectNode(JsonElement project)
    {
        if (_projectTreePanel == null)
        {
            return;
        }

        var projectId = GetString(project, "id");
        var projectName = FirstNonEmpty(GetString(project, "name"), "未命名项目");
        var runs = project.TryGetProperty("check_runs", out var runItems) && runItems.ValueKind == JsonValueKind.Array
            ? runItems.EnumerateArray().ToList()
            : new List<JsonElement>();
        var expanded = projectId == _selectedProjectId;
        var projectButton = new Button
        {
            Content = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                Children = { Text(expanded ? "▼" : "▶", 12, FontWeights.Bold, FindBrush("MutedBrush"), new Thickness(0, 0, 8, 0)), Text(projectName, 14, FontWeights.SemiBold, FindBrush("TextBrush")) }
            },
            Style = (Style)FindResource("SecondaryButton"),
            HorizontalContentAlignment = HorizontalAlignment.Left,
            Padding = new Thickness(10, 9, 10, 9),
            Margin = new Thickness(0, 0, 0, 4),
            Tag = projectId
        };
        projectButton.Click += (_, _) =>
        {
            _selectedProjectId = expanded ? null : projectId;
            _selectedProjectName = projectName;
            if (expanded)
            {
                _selectedCheckRunId = null;
                _activeRecordId = null;
                ResetRecordDetail("请选择一个项目和报告检查查看结果。");
                ResetDocumentPreview("选择检查项后加载完整原文。");
            }
            else if (_selectedCheckRunId != null)
            {
                _selectedCheckRunId = null;
            }
            UpdateDeleteSelectionButtonState();
            RenderProjectTreeFromCurrentData();
        };
        var projectRow = new Grid { Margin = new Thickness(0, 0, 0, 4) };
        projectRow.ColumnDefinitions.Add(new ColumnDefinition());
        projectRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        projectButton.Margin = new Thickness(0);
        projectRow.Children.Add(projectButton);
        _projectTreePanel.Children.Add(projectRow);

        if (!expanded)
        {
            return;
        }

        if (runs.Count == 0)
        {
            _projectTreePanel.Children.Add(Text("暂无报告检查记录。", 12, null, FindBrush("MutedBrush"), new Thickness(28, 2, 0, 8), true));
            return;
        }

        foreach (var run in runs)
        {
            var runId = GetString(run, "id");
            var isSelected = runId == _selectedCheckRunId;
            var runName = FirstNonEmpty(GetString(run, "report_name"), "未命名报告");
            var createdAt = FormatDateTime(GetString(run, "created_at"));
            var resultCount = run.TryGetProperty("results", out var resultItems) && resultItems.ValueKind == JsonValueKind.Array
                ? resultItems.GetArrayLength()
                : 0;
            var runStack = new StackPanel();
            runStack.Children.Add(Text($"{(isSelected ? "▼" : "▶")}  {runName}", 13, FontWeights.SemiBold, FindBrush("TextBrush"), null, true));
            runStack.Children.Add(Text($"{createdAt} · {resultCount}/9 项检查", 12, null, FindBrush("MutedBrush"), new Thickness(0, 3, 0, 0), true));
            var runButton = new Button
            {
                Content = runStack,
                Style = (Style)FindResource("SecondaryButton"),
                Background = isSelected ? Brush(239, 246, 255) : Brushes.White,
                BorderBrush = isSelected ? Brush(191, 219, 254) : Brush(234, 236, 240),
                HorizontalContentAlignment = HorizontalAlignment.Left,
                Padding = new Thickness(10, 8, 10, 8),
                Margin = new Thickness(24, 0, 0, 5),
                Tag = runId
            };
            // JsonElement is backed by the response JsonDocument, which is disposed
            // when LoadRecordsAsync returns. Keep an owned snapshot for the click handler.
            var runSnapshot = run.Clone();
            runButton.Click += (_, _) =>
            {
                if (isSelected)
                {
                    _selectedCheckRunId = null;
                    _activeRecordId = null;
                    UpdateDeleteSelectionButtonState();
                    RenderProjectTreeFromCurrentData();
                    ResetRecordDetail("请展开一份报告并选择检查项。");
                    ResetDocumentPreview("选择检查项后加载完整原文。");
                    return;
                }
                SelectCheckRun(runId, runSnapshot);
            };
            _projectTreePanel.Children.Add(runButton);

            if (isSelected)
            {
                RenderRunResultNodes(ParseRunResults(run));
            }
        }
    }

    private void RenderRunResultNodes(IReadOnlyCollection<RunResultInfo> results)
    {
        if (_projectTreePanel == null)
        {
            return;
        }

        var orderedResults = results
            .Where(item => !string.IsNullOrWhiteSpace(item.Module))
            .OrderBy(item =>
            {
                var index = Array.FindIndex(_checkItems, checkItem => checkItem.ModuleCode == item.Module);
                return index < 0 ? int.MaxValue : index;
            })
            .ToList();

        foreach (var result in orderedResults)
        {
            var moduleTitle = !string.IsNullOrWhiteSpace(result.ModuleName) && result.ModuleName != result.Module
                ? result.ModuleName
                : ModuleDisplayName(result.Module);
            var isActive = result.Id == _activeRecordId;
            var content = new StackPanel();
            content.Children.Add(Text(moduleTitle, 12, FontWeights.SemiBold, FindBrush("TextBrush"), null, true));
            content.Children.Add(Text($"{FirstNonEmpty(result.Status, "已完成")} · 问题：{result.FindingsCount}", 11, null, Brush(2, 122, 72), new Thickness(0, 3, 0, 0), true));
            var button = new Button
            {
                Content = content,
                Style = (Style)FindResource("SecondaryButton"),
                Background = isActive ? Brush(239, 246, 255) : Brushes.White,
                BorderBrush = isActive ? Brush(96, 165, 250) : Brush(234, 236, 240),
                HorizontalContentAlignment = HorizontalAlignment.Left,
                Padding = new Thickness(10, 7, 10, 7),
                Margin = new Thickness(48, 0, 0, 5),
                IsEnabled = !string.IsNullOrWhiteSpace(result.Id),
                ToolTip = "查看审查结果和报告原文"
            };
            if (!string.IsNullOrWhiteSpace(result.Id))
            {
                button.Click += async (_, _) => await LoadRecordDetailAsync(result.Id);
            }
            _projectTreePanel.Children.Add(button);
        }

        if (orderedResults.Count == 0)
        {
            _projectTreePanel.Children.Add(Text("本次报告暂无已完成的检查结果。", 12, null, FindBrush("MutedBrush"), new Thickness(48, 2, 0, 8), true));
        }
    }

    private async Task DeleteCheckRunAsync(string projectId, string runId, string runName)
    {
        if (string.IsNullOrWhiteSpace(projectId) || string.IsNullOrWhiteSpace(runId))
        {
            return;
        }

        var confirm = MessageBox.Show(
            this,
            runId.StartsWith("legacy:", StringComparison.OrdinalIgnoreCase)
                ? $"确定删除历史检查记录“{runName}”吗？该记录组下的结果都会删除，项目和其他检查不受影响。"
                : $"确定删除这次检查“{runName}”吗？该次检查下的所有模块结果都会删除，项目和其他检查不受影响。",
            "删除本次检查",
            MessageBoxButton.OKCancel,
            MessageBoxImage.Warning);
        if (confirm != MessageBoxResult.OK)
        {
            return;
        }

        try
        {
            using var response = await BackendClient.DeleteAsync(
                $"{BackendBaseUrl}/projects/{Uri.EscapeDataString(projectId)}/check-runs/{Uri.EscapeDataString(runId)}");
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                MessageBox.Show(this, $"删除本次检查失败：{ExtractErrorMessage(responseBody)}", "删除失败", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (_selectedCheckRunId == runId)
            {
                _selectedCheckRunId = null;
                _activeRecordId = null;
                _runResults.Remove(runId);
                ResetRecordDetail("请选择一个项目和报告检查查看结果。");
                ResetDocumentPreview("选择检查项后加载完整原文。");
            }

            UpdateDeleteSelectionButtonState();
            await LoadRecordsAsync();
            StatusTextBlock.Text = $"检查“{runName}”已删除。";
        }
        catch (Exception exc)
        {
            MessageBox.Show(this, $"删除本次检查失败：{exc.Message}", "删除失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async Task DeleteSelectedProjectOrRunAsync()
    {
        if (!string.IsNullOrWhiteSpace(_selectedCheckRunId)
            && !string.IsNullOrWhiteSpace(_selectedProjectId))
        {
            var runName = FindSelectedRunName(_selectedProjectId, _selectedCheckRunId);
            await DeleteCheckRunAsync(_selectedProjectId, _selectedCheckRunId, runName);
            return;
        }

        if (!string.IsNullOrWhiteSpace(_selectedProjectId))
        {
            await DeleteProjectAsync(
                _selectedProjectId,
                FirstNonEmpty(_selectedProjectName ?? string.Empty, "未命名项目"));
        }
    }

    private string FindSelectedRunName(string projectId, string runId)
    {
        if (_projectTreeRoot.HasValue
            && _projectTreeRoot.Value.TryGetProperty("items", out var items)
            && items.ValueKind == JsonValueKind.Array)
        {
            foreach (var project in items.EnumerateArray())
            {
                if (!string.Equals(GetString(project, "id"), projectId, StringComparison.OrdinalIgnoreCase)
                    || !project.TryGetProperty("check_runs", out var runs)
                    || runs.ValueKind != JsonValueKind.Array)
                {
                    continue;
                }

                foreach (var run in runs.EnumerateArray())
                {
                    if (string.Equals(GetString(run, "id"), runId, StringComparison.OrdinalIgnoreCase))
                    {
                        return FirstNonEmpty(GetString(run, "report_name"), "未命名报告");
                    }
                }
            }
        }

        return "未命名报告";
    }

    private void UpdateDeleteSelectionButtonState()
    {
        if (_deleteSelectionButton == null)
        {
            return;
        }

        var hasRun = !string.IsNullOrWhiteSpace(_selectedCheckRunId);
        var hasProject = !string.IsNullOrWhiteSpace(_selectedProjectId);
        _deleteSelectionButton.IsEnabled = hasRun || hasProject;
        _deleteSelectionButton.ToolTip = hasRun
            ? "删除当前选中的检查记录"
            : hasProject
                ? "删除当前选中的项目及其全部检查记录"
                : "请先选择项目或检查记录";
    }

    private JsonElement? _projectTreeRoot;

    private void RenderProjectTreeFromCurrentData()
    {
        if (_projectTreeRoot.HasValue)
        {
            RenderProjectTree(_projectTreeRoot.Value);
        }
    }

    private void SelectCheckRun(string runId, JsonElement run)
    {
        _selectedCheckRunId = runId;
        UpdateDeleteSelectionButtonState();
        _runResults[runId] = ParseRunResults(run);
        RenderProjectTreeFromCurrentData();
        ResetRecordDetail("请选择一项检查查看详细结果。");
        ResetDocumentPreview("选择第三级检查项后加载完整原文。");
    }

    private List<RunResultInfo> ParseRunResults(JsonElement run)
    {
        if (!run.TryGetProperty("results", out var results) || results.ValueKind != JsonValueKind.Array)
        {
            return new List<RunResultInfo>();
        }

        return results.EnumerateArray()
            .Select(item => new RunResultInfo(
                GetString(item, "id"),
                GetString(item, "module"),
                GetString(item, "module_name"),
                GetString(item, "status"),
                GetString(item, "risk_level"),
                GetInt(item, "findings_count"),
                GetString(item, "created_at")))
            .ToList();
    }

    private void RenderCheckModules(IReadOnlyCollection<RunResultInfo> results)
    {
        RenderProjectTreeFromCurrentData();
    }

    private async Task LoadRecordDetailAsync(string resultId)
    {
        if (_recordDetailPanel == null)
        {
            return;
        }

        _recordDetailPanel.Children.Clear();
        _recordDetailPanel.Children.Add(Text("正在加载详情...", 13, null, FindBrush("MutedBrush"), null, true));

        try
        {
            using var response = await BackendClient.GetAsync($"{BackendBaseUrl}/evaluate/results/{resultId}");
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                _recordDetailPanel.Children.Clear();
                _recordDetailPanel.Children.Add(Text($"加载失败：{ExtractErrorMessage(responseBody)}", 13, null, Brush(180, 35, 24), null, true));
                return;
            }

            using var document = JsonDocument.Parse(responseBody);
            RenderRecordDetail(document.RootElement);
            RenderProjectTreeFromCurrentData();
            await LoadDocumentPreviewAsync(document.RootElement);
        }
        catch (Exception exc)
        {
            _recordDetailPanel.Children.Clear();
            _recordDetailPanel.Children.Add(Text($"加载失败：{exc.Message}", 13, null, Brush(180, 35, 24), null, true));
        }
    }

    private void RenderRecordDetail(JsonElement root)
    {
        if (_recordDetailPanel == null)
        {
            return;
        }

        _recordDetailPanel.Children.Clear();
        var id = GetString(root, "id");
        _activeRecordId = id;
        var module = GetString(root, "module");
        var moduleName = GetString(root, "module_name");
        var projectName = FirstNonEmpty(GetString(root, "project_name"), GetString(root, "report_name"), "未命名报告");
        var resultRoot = root.TryGetProperty("result", out var resultElement) && resultElement.ValueKind == JsonValueKind.Object
            ? resultElement
            : root;
        var risk = GetString(root, "risk_level");
        var count = GetInt(root, "findings_count");
        if (resultRoot.TryGetProperty("rule_results_summary", out var ruleResultsSummary)
            && ruleResultsSummary.ValueKind == JsonValueKind.Object)
        {
            count = GetInt(ruleResultsSummary, "total_findings");
        }

        _recordDetailPanel.Children.Add(Text(projectName, 18, FontWeights.Bold, FindBrush("TextBrush"), null, true));
        _recordDetailPanel.Children.Add(Text($"检查模块：{moduleName}", 13, FontWeights.SemiBold, FindBrush("TextBrush"), new Thickness(0, 6, 0, 0), true));
        _recordDetailPanel.Children.Add(Text($"风险等级：{risk}｜问题数量：{count}", 13, null, FindBrush("MutedBrush"), new Thickness(0, 4, 0, 8), true));

        if (root.TryGetProperty("summary", out var summary) && summary.ValueKind == JsonValueKind.Object)
        {
            var llmModelText = LlmModelText(summary);
            if (!string.IsNullOrWhiteSpace(llmModelText))
            {
                _recordDetailPanel.Children.Add(Text(llmModelText, 13, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 12), true));
            }
        }

        RenderResultNotices(_recordDetailPanel, resultRoot);

        if (module == "duplicate" && TryRenderDuplicateFindings(_recordDetailPanel, resultRoot))
        {
            // 重复建设结果按内部重复和跨报告重复分组展示。
        }
        else if (TryRenderMergedRuleFindings(_recordDetailPanel, resultRoot, module))
        {
            // 已合并展示小规则 findings，不再重复渲染顶层 findings。
        }
        else if (resultRoot.TryGetProperty("findings", out var findings) && findings.ValueKind == JsonValueKind.Array && findings.GetArrayLength() > 0)
        {
            foreach (var finding in findings.EnumerateArray())
            {
                _recordDetailPanel.Children.Add(module == "sensitive_word"
                    ? BuildSensitiveFindingCard(finding)
                    : BuildFunctionFindingCard(finding));
            }
        }
        else
        {
            _recordDetailPanel.Children.Add(Text("未发现需要复核的问题。", 13, null, Brush(2, 122, 72), null, true));
        }

        var expander = new Expander
        {
            Header = "查看技术详情 / 原始 JSON",
            IsExpanded = false,
            Margin = new Thickness(0, 8, 0, 0),
            Content = BuildTechnicalJsonViewer(root)
        };
        _recordDetailPanel.Children.Add(expander);
    }

    private async Task LoadDocumentPreviewAsync(JsonElement root)
    {
        if (_documentWebView == null)
        {
            return;
        }

        if (!root.TryGetProperty("source_documents", out var sources)
            || sources.ValueKind != JsonValueKind.Array
            || sources.GetArrayLength() == 0)
        {
            ResetDocumentPreview("这条历史记录没有保存原文件，请重新执行一次检查后预览。");
            return;
        }

        var source = sources.EnumerateArray()
            .FirstOrDefault(item => GetString(item, "role") == "current");
        if (source.ValueKind != JsonValueKind.Object)
        {
            source = sources.EnumerateArray().First();
        }

        _previewUrlsByRole.Clear();
        _previewNamesByRole.Clear();
        foreach (var sourceItem in sources.EnumerateArray())
        {
            var role = FirstNonEmpty(GetString(sourceItem, "role"), "current");
            var path = GetString(sourceItem, "preview_url");
            if (string.IsNullOrWhiteSpace(path))
            {
                var download = GetString(sourceItem, "download_url");
                path = string.IsNullOrWhiteSpace(download) ? string.Empty : $"{download}/preview";
            }
            if (!string.IsNullOrWhiteSpace(path))
            {
                _previewUrlsByRole[role] = BuildBackendUrl(path);
                _previewNamesByRole[role] = FirstNonEmpty(GetString(sourceItem, "filename"), "原始报告");
                if (role.Equals("history", StringComparison.OrdinalIgnoreCase))
                {
                    _previewUrlsByRole["related"] = _previewUrlsByRole[role];
                    _previewNamesByRole["related"] = _previewNamesByRole[role];
                }
            }
        }

        var filename = FirstNonEmpty(GetString(source, "filename"), "原始报告");
        var previewPath = GetString(source, "preview_url");
        if (string.IsNullOrWhiteSpace(previewPath))
        {
            var downloadPath = GetString(source, "download_url");
            previewPath = string.IsNullOrWhiteSpace(downloadPath) ? string.Empty : $"{downloadPath}/preview";
        }
        if (string.IsNullOrWhiteSpace(previewPath))
        {
            ResetDocumentPreview("原文预览地址不可用。");
            return;
        }

        _activePreviewUrl = BuildBackendUrl(previewPath);
        _previewUrlsByRole["current"] = _activePreviewUrl;
        _previewNamesByRole["current"] = filename;
        _activePreviewExtension = Path.GetExtension(filename).ToLowerInvariant();
        if (_documentTitleTextBlock != null)
        {
            _documentTitleTextBlock.Text = filename;
        }
        if (_documentLocationTextBlock != null)
        {
            _documentLocationTextBlock.Text = "Word 完整原文预览";
        }

        try
        {
            await _documentWebView.EnsureCoreWebView2Async();
            _documentWebView.Source = new Uri(_activePreviewUrl);
        }
        catch (Exception exc)
        {
            ResetDocumentPreview($"原文加载失败：{exc.Message}");
        }
    }

    private async Task SwitchPreviewSourceAsync(string role)
    {
        if (_documentWebView == null || !_previewUrlsByRole.TryGetValue(role, out var url))
        {
            return;
        }

        _activePreviewUrl = url;
        _activePreviewExtension = Path.GetExtension(_previewNamesByRole.GetValueOrDefault(role, "原始报告")).ToLowerInvariant();
        if (_documentTitleTextBlock != null)
        {
            _documentTitleTextBlock.Text = _previewNamesByRole.GetValueOrDefault(role, "原始报告");
        }
        if (_documentLocationTextBlock != null)
        {
            _documentLocationTextBlock.Text = role.Equals("related", StringComparison.OrdinalIgnoreCase)
                ? "关联 Word 原文预览"
                : "Word 完整原文预览";
        }
        await _documentWebView.EnsureCoreWebView2Async();
        _documentWebView.Source = new Uri(url);
        await Task.Delay(120);
    }

    private async Task NavigateToFindingAsync(JsonElement finding, string? preferredRole = null)
    {
        if (_documentWebView == null || string.IsNullOrWhiteSpace(_activePreviewUrl))
        {
            return;
        }

        var quote = string.Empty;
        var section = GetString(finding, "source_section");
        var page = 0;
        var paragraph = 0;
        var line = 0;

        var requestedRole = string.IsNullOrWhiteSpace(preferredRole) ? "current" : preferredRole;
        if (finding.TryGetProperty("source_highlights", out var highlights) && highlights.ValueKind == JsonValueKind.Array)
        {
            var highlight = highlights.EnumerateArray()
                .FirstOrDefault(item => !string.IsNullOrWhiteSpace(requestedRole)
                    && string.Equals(GetString(item, "role"), requestedRole, StringComparison.OrdinalIgnoreCase));
            if (highlight.ValueKind != JsonValueKind.Object)
            {
                highlight = highlights.EnumerateArray()
                    .FirstOrDefault(item => GetString(item, "role") is "current" or "evidence_1");
            }
            if (highlight.ValueKind != JsonValueKind.Object)
            {
                highlight = highlights.EnumerateArray().FirstOrDefault();
            }
            if (highlight.ValueKind == JsonValueKind.Object)
            {
                quote = GetString(highlight, "quote");
                section = FirstNonEmpty(GetString(highlight, "section"), section);
                page = GetInt(highlight, "page");
                paragraph = GetInt(highlight, "paragraph");
                line = GetInt(highlight, "line_start");
            }
        }

        if (finding.TryGetProperty("source_location", out var location) && location.ValueKind == JsonValueKind.Object)
        {
            if (!string.Equals(requestedRole, "related", StringComparison.OrdinalIgnoreCase)
                && location.TryGetProperty("current", out var currentLocation) && currentLocation.ValueKind == JsonValueKind.Object)
            {
                location = currentLocation;
            }
            else if (string.Equals(requestedRole, "related", StringComparison.OrdinalIgnoreCase)
                && location.TryGetProperty("related", out var relatedLocation) && relatedLocation.ValueKind == JsonValueKind.Object)
            {
                location = relatedLocation;
            }
            quote = FirstNonEmpty(quote, GetString(location, "quote"));
            section = FirstNonEmpty(section, GetString(location, "section"));
            page = page > 0 ? page : GetInt(location, "page");
            paragraph = paragraph > 0 ? paragraph : GetInt(location, "paragraph");
            line = line > 0 ? line : GetInt(location, "line_start");
        }

        quote = FirstNonEmpty(
            quote,
            GetString(finding, "item_source_excerpt"),
            GetString(finding, "evidence"),
            GetString(finding, "context"),
            GetString(finding, "hit_text"));

        try
        {
            await _documentWebView.EnsureCoreWebView2Async();
            if (!string.IsNullOrWhiteSpace(requestedRole)
                && _previewUrlsByRole.TryGetValue(requestedRole, out var requestedUrl)
                && !string.Equals(_activePreviewUrl, requestedUrl, StringComparison.OrdinalIgnoreCase))
            {
                await SwitchPreviewSourceAsync(requestedRole);
            }
            var script = $"window.locateSource({JsonSerializer.Serialize(quote)}, {paragraph}, {line}, {JsonSerializer.Serialize(section)})";
            var result = await _documentWebView.ExecuteScriptAsync(script);
            if (_documentLocationTextBlock != null)
            {
                _documentLocationTextBlock.Text = result.Contains("true", StringComparison.OrdinalIgnoreCase)
                    ? "已定位并高亮对应原文"
                    : "未匹配到完整摘录，已保留章节或最近原文位置";
            }
        }
        catch (Exception exc)
        {
            if (_documentLocationTextBlock != null)
            {
                _documentLocationTextBlock.Text = $"定位失败：{exc.Message}";
            }
        }
    }

    private void ResetDocumentPreview(string message)
    {
        _activePreviewUrl = null;
        _activePreviewExtension = null;
        _previewUrlsByRole.Clear();
        _previewNamesByRole.Clear();
        if (_documentTitleTextBlock != null)
        {
            _documentTitleTextBlock.Text = "报告原文";
        }
        if (_documentLocationTextBlock != null)
        {
            _documentLocationTextBlock.Text = message;
        }
        _ = ShowPreviewPlaceholderAsync(message);
    }

    private async Task ShowPreviewPlaceholderAsync(string message)
    {
        if (_documentWebView == null)
        {
            return;
        }
        try
        {
            await _documentWebView.EnsureCoreWebView2Async();
            var encoded = System.Net.WebUtility.HtmlEncode(message);
            _documentWebView.NavigateToString($"<!doctype html><html><body style='margin:0;background:#eef2f7;font-family:Microsoft YaHei,Segoe UI,sans-serif;color:#64748b;display:grid;place-items:center;height:100vh'><div style='padding:28px;text-align:center'>{encoded}</div></body></html>");
        }
        catch
        {
            // The status header still reports the preview state when WebView2 is unavailable.
        }
    }

    private static string BuildBackendUrl(string path)
    {
        if (Uri.TryCreate(path, UriKind.Absolute, out var absolute))
        {
            return absolute.ToString();
        }
        return new Uri(new Uri("http://127.0.0.1:8000"), path).ToString();
    }

    private UIElement BuildTechnicalJsonViewer(JsonElement root)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 8, 0, 0) };
        panel.Children.Add(Text(
            "定位字段已标色：修改建议/位置为蓝色，原文摘录/高亮标记为黄色。",
            12,
            null,
            FindBrush("MutedBrush"),
            new Thickness(0, 0, 0, 6),
            true));

        string json;
        try
        {
            // Parse first so \uXXXX escape sequences are decoded before the
            // technical view is rendered, then keep Chinese characters intact.
            var displayNode = JsonNode.Parse(root.GetRawText());
            json = displayNode?.ToJsonString(new JsonSerializerOptions
            {
                WriteIndented = true,
                Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            }) ?? root.GetRawText();
        }
        catch (JsonException)
        {
            json = root.GetRawText();
        }
        var viewer = new RichTextBox
        {
            IsReadOnly = true,
            IsDocumentEnabled = false,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
            Background = Brush(248, 250, 252),
            BorderBrush = Brush(226, 232, 240),
            BorderThickness = new Thickness(1),
            Padding = new Thickness(8),
            FontFamily = new System.Windows.Media.FontFamily("Consolas"),
            FontSize = 12,
            MaxHeight = 460
        };

        foreach (var line in json.Split('\n'))
        {
            var trimmed = line.Trim();
            var run = new Run(line + Environment.NewLine);
            if (trimmed.Contains("\"revision_advice\"")
                || trimmed.Contains("\"location_hint\"")
                || trimmed.Contains("\"revision_target\""))
            {
                run.Background = Brush(219, 234, 254);
                run.Foreground = Brush(30, 64, 175);
            }
            else if (trimmed.Contains("\"source_highlights\"")
                || trimmed.Contains("\"quote\"")
                || trimmed.Contains("\"highlight\"")
                || trimmed.Contains("\"source_location\""))
            {
                run.Background = Brush(255, 247, 214);
                run.Foreground = Brush(120, 53, 15);
            }
            viewer.Document.Blocks.Add(new Paragraph(run)
            {
                Margin = new Thickness(0),
                LineHeight = 18
            });
        }

        panel.Children.Add(viewer);
        return panel;
    }

    private async Task DeleteRecordAsync(string resultId)
    {
        if (string.IsNullOrWhiteSpace(resultId))
        {
            return;
        }

        var confirm = MessageBox.Show(
            this,
            "确定删除这条审查记录吗？此操作不会删除原始报告文件。",
            "删除审查记录",
            MessageBoxButton.OKCancel,
            MessageBoxImage.Warning);
        if (confirm != MessageBoxResult.OK)
        {
            return;
        }

        try
        {
            using var response = await BackendClient.DeleteAsync($"{BackendBaseUrl}/evaluate/results/{resultId}");
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                MessageBox.Show(this, $"删除失败：{ExtractErrorMessage(responseBody)}", "删除失败", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            if (_activeRecordId == resultId)
            {
                ResetRecordDetail();
            }

            await LoadRecordsAsync();
            StatusTextBlock.Text = "审查记录已删除。";
        }
        catch (Exception exc)
        {
            MessageBox.Show(this, $"删除失败：{exc.Message}", "删除失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private void ResetRecordDetail(string message = "请选择一条记录查看详情。")
    {
        _activeRecordId = null;
        if (_recordDetailPanel == null)
        {
            return;
        }

        _recordDetailPanel.Children.Clear();
        _recordDetailPanel.Children.Add(Text("结果详情", 18, FontWeights.Bold));
        _recordDetailPanel.Children.Add(Text(message, 13, null, FindBrush("MutedBrush"), new Thickness(0, 6, 0, 0), true));
    }

    private void RenderRuleDirectory()
    {
        if (_rulesDirectoryPanel == null)
        {
            return;
        }

        _rulesDirectoryPanel.Children.Clear();
        _rulesDirectoryPanel.Children.Add(Text("规则目录", 18, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(0, 0, 0, 6)));
        _rulesDirectoryPanel.Children.Add(Text("按审查场景组织，快速定位规则集。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 14), true));
        AddRuleDirectoryItem("basis", "建设依据审查");
        AddRuleDirectoryItem("function_correspondence", "建设功能的对应关系检查");
        AddRuleDirectoryItem("sensitive_word", "敏感词检查");
        AddRuleDirectoryItem("data_reasonableness", "数据填报合理性检查");
        AddRuleDirectoryItem("resource", "资源申请合理性检查");
        AddRuleDirectoryItem("price", "价格合理性");
        AddRuleDirectoryItem("price_reference", "软硬件价格参考");
        AddRuleDirectoryItem("security", "安全内容的合理性");
    }

    private void AddRuleDirectoryItem(string moduleCode, string title)
    {
        if (_rulesDirectoryPanel == null)
        {
            return;
        }

        var count = _rulesByModule.TryGetValue(moduleCode, out var rules) ? rules.Count : 0;
        var item = RuleItem(title, $"{count} 条规则", count > 0 ? "已启用" : "未加载", count > 0 ? Brush(2, 122, 72) : Brush(181, 71, 8), moduleCode == _activeRuleModule);
        item.MouseLeftButtonUp += (_, _) =>
        {
            _activeRuleModule = moduleCode;
            RenderRuleDirectory();
            RenderRuleModule(moduleCode);
        };
        _rulesDirectoryPanel.Children.Add(item);
    }

    private void RenderRuleModule(string moduleCode)
    {
        if (_rulesEditorPanel == null)
        {
            return;
        }

        _rulesEditorPanel.Children.Clear();
        var moduleName = ModuleDisplayName(moduleCode);
        MergeBuiltInRules(moduleCode);
        var rules = _rulesByModule.TryGetValue(moduleCode, out var loadedRules) ? loadedRules : new List<RuleDisplayItem>();
        _rulesEditorPanel.Children.Add(Text(moduleName, 18, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(0, 0, 0, 6)));
        _rulesEditorPanel.Children.Add(Text("每条规则默认勾选，开始检测时会把所选规则 ID 传给后端。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 14), true));

        var chips = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        chips.Children.Add(Pill("启用中", Brush(2, 122, 72), Brush(236, 253, 243)));
        chips.Children.Add(Pill($"{rules.Count} 条规则", Brush(37, 99, 235), Brush(239, 246, 255)));
        chips.Children.Add(Pill(RuleSourceDisplayName(rules.FirstOrDefault()?.Source), Brush(181, 71, 8), Brush(255, 250, 235)));
        _rulesEditorPanel.Children.Add(chips);

        var checkBoxes = new List<CheckBox>();
        var selectedRuleIds = _ruleCheckBoxesByModule.TryGetValue(moduleCode, out var existingCheckBoxes)
            ? existingCheckBoxes
                .Where(item => item.IsChecked == true)
                .Select(item => item.Tag?.ToString())
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .Cast<string>()
                .ToHashSet()
            : null;
        foreach (var rule in rules)
        {
            var checkBox = new CheckBox
            {
                Content = RuleDisplayContent(moduleCode, rule.RuleName, rule.RuleCategory),
                IsChecked = selectedRuleIds == null || selectedRuleIds.Contains(rule.RuleId),
                Tag = rule.RuleId,
                FontWeight = FontWeights.SemiBold,
                Margin = new Thickness(0, 0, 0, 4)
            };
            checkBoxes.Add(checkBox);
            _rulesEditorPanel.Children.Add(checkBox);
            if (!string.IsNullOrWhiteSpace(rule.RuleDetail))
            {
                _rulesEditorPanel.Children.Add(Text(rule.RuleDetail, 12, null, FindBrush("MutedBrush"), new Thickness(22, 0, 0, 8), true));
            }
        }

        if (checkBoxes.Count == 0)
        {
            _rulesEditorPanel.Children.Add(Text("未读取到该模块规则，请确认后端规则接口或本地 Excel fallback。", 13, null, Brush(181, 71, 8), null, true));
        }

        _ruleCheckBoxesByModule[moduleCode] = checkBoxes;
        if (_ruleStatsTextBlock != null)
        {
            _ruleStatsTextBlock.Text = _rulesByModule.Values.Sum(item => item.Count).ToString();
        }
    }

    private static string ModuleDisplayName(string moduleCode) => moduleCode switch
    {
        "basis" => "建设依据审查",
        "function_correspondence" => "建设功能的对应关系检查",
        "duplicate" => "重复建设检查",
        "sensitive_word" => "敏感词检查",
        "data_reasonableness" => "数据填报合理性检查",
        "resource" => "资源申请合理性检查",
        "price" => "价格合理性",
        "price_reference" => "软硬件价格参考",
        "security" => "安全内容的合理性",
        _ => moduleCode
    };

    private static string RuleSourceDisplayName(string? source) => source switch
    {
        "rule_api" => "规则库 API",
        "local_builtin" => "本地 fallback",
        _ => "本地 fallback"
    };

    private static string RuleDisplayContent(string moduleCode, string ruleName, string category)
    {
        return string.IsNullOrWhiteSpace(category) ? ruleName : $"{ruleName}（{category}）";
    }

    private async Task<string> CreateEvaluateTaskAsync(
        IReadOnlyCollection<CheckItem> items,
        string reportPath,
        string projectId,
        string checkRunId)
    {
        await using var stream = File.OpenRead(reportPath);
        using var form = new MultipartFormDataContent();
        using var fileContent = new StreamContent(stream);
        fileContent.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");

        var modules = string.Join(",", items.Select(item => item.ModuleCode));
        var selectedRules = items.ToDictionary(
            item => item.ModuleCode,
            item => SelectedRuleIdsForModule(item.ModuleCode).ToArray());

        form.Add(fileContent, "file", Path.GetFileName(reportPath));
        form.Add(new StringContent(projectId, Encoding.UTF8), "project_id");
        form.Add(new StringContent(checkRunId, Encoding.UTF8), "check_run_id");
        form.Add(new StringContent(FirstNonEmpty(_selectedProjectName ?? string.Empty, Path.GetFileNameWithoutExtension(reportPath)), Encoding.UTF8), "project_name");
        form.Add(new StringContent(string.Empty, Encoding.UTF8), "department");
        form.Add(new StringContent(modules, Encoding.UTF8), "modules");
        form.Add(new StringContent("api", Encoding.UTF8), "rule_source");
        form.Add(new StringContent((_llmReviewCheckBox?.IsChecked == true).ToString().ToLowerInvariant(), Encoding.UTF8), "use_llm");
        form.Add(new StringContent(JsonSerializer.Serialize(selectedRules), Encoding.UTF8), "selected_rule_ids");

        using var response = await BackendClient.PostAsync($"{BackendBaseUrl}/evaluate/tasks", form);
        var responseBody = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException($"创建检测任务失败：{ExtractErrorMessage(responseBody)}");
        }

        using var document = JsonDocument.Parse(responseBody);
        var taskId = GetString(document.RootElement, "task_id");
        if (string.IsNullOrWhiteSpace(taskId))
        {
            throw new InvalidOperationException("后端未返回检测任务编号。");
        }

        return taskId;
    }

    private async Task PollEvaluateTaskAsync(string taskId)
    {
        for (var attempt = 0; attempt < 1800; attempt++)
        {
            await Task.Delay(TimeSpan.FromSeconds(2));
            using var response = await BackendClient.GetAsync($"{BackendBaseUrl}/evaluate/tasks/{taskId}");
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                throw new InvalidOperationException($"查询检测任务失败：{ExtractErrorMessage(responseBody)}");
            }

            using var document = JsonDocument.Parse(responseBody);
            var root = document.RootElement;
            var status = GetString(root, "status");
            var stage = GetString(root, "stage");
            var message = FirstNonEmpty(GetString(root, "message"), "正在后台审查");
            var progress = GetInt(root, "progress");

            if (status is "queued" or "running")
            {
                StatusTextBlock.Text = $"{TaskStatusText(status)}：{message}（{progress}%）";
                continue;
            }

            await LoadRecordsAsync();
            if (IsCompletedTaskStatus(status) || HasResultIds(root))
            {
                StatusTextBlock.Text = "检测完成，请在“审查记录”查看结果。";
                AddResultNotice("检测完成，请在“审查记录”查看企业可读审查意见。");
                return;
            }

            var error = GetString(root, "error");
            StatusTextBlock.Text = "检测失败。";
            AddResultNotice($"检测失败：{FirstNonEmpty(error, message)}");
            return;
        }

        StatusTextBlock.Text = "检测仍在后台执行，请稍后刷新审查记录。";
        AddResultNotice("检测任务仍在后台执行，请稍后进入“审查记录”刷新查看。");
    }

    private static string TaskStatusText(string status) => status switch
    {
        "queued" => "等待检测",
        "running" => "正在检测",
        "completed" => "检测完成",
        "partial" => "检测完成",
        "failed" => "检测失败",
        _ => "检测任务"
    };

    private static bool IsCompletedTaskStatus(string status)
    {
        var normalized = status.Trim().ToLowerInvariant();
        return normalized is "success" or "completed" or "done" or "partial" or "timeout-with-result";
    }

    private static bool HasResultIds(JsonElement root)
    {
        return root.TryGetProperty("result_ids", out var resultIds)
            && resultIds.ValueKind == JsonValueKind.Array
            && resultIds.GetArrayLength() > 0;
    }

    private async Task<JsonDocument> UploadAndRunCheckAsync(CheckItem item, string reportPath)
    {
        await using var stream = File.OpenRead(reportPath);
        using var form = new MultipartFormDataContent();
        using var fileContent = new StreamContent(stream);
        fileContent.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");

        form.Add(fileContent, "file", Path.GetFileName(reportPath));
        form.Add(new StringContent(Path.GetFileNameWithoutExtension(reportPath), Encoding.UTF8), "project_name");
        form.Add(new StringContent(string.Empty, Encoding.UTF8), "department");
        form.Add(new StringContent("api", Encoding.UTF8), "rule_source");
        form.Add(new StringContent(ProjectLevelForModule(item.ModuleCode), Encoding.UTF8), "project_level");
        var useLlm = item.ModuleCode == "security" || _llmReviewCheckBox?.IsChecked == true;
        form.Add(new StringContent(useLlm.ToString().ToLowerInvariant(), Encoding.UTF8), "use_llm");
        form.Add(new StringContent(string.Join(",", SelectedRuleIdsForModule(item.ModuleCode)), Encoding.UTF8), "selected_rule_ids");

        using var response = await BackendClient.PostAsync(EndpointForModule(item.ModuleCode), form);
        var responseBody = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException($"{item.Title} 请求失败：{ExtractErrorMessage(responseBody)}");
        }

        return JsonDocument.Parse(responseBody);
    }

    private async Task<JsonDocument> UploadAndRunDuplicateCompareAsync(
        string currentReportPath,
        IReadOnlyCollection<string> historyPaths,
        string projectId,
        string checkRunId)
    {
        await using var currentStream = File.OpenRead(currentReportPath);
        using var form = new MultipartFormDataContent();
        using var currentContent = new StreamContent(currentStream);
        currentContent.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");

        form.Add(currentContent, "current_file", Path.GetFileName(currentReportPath));
        form.Add(new StringContent(projectId, Encoding.UTF8), "project_id");
        form.Add(new StringContent(checkRunId, Encoding.UTF8), "check_run_id");
        form.Add(new StringContent(FirstNonEmpty(_selectedProjectName ?? string.Empty, Path.GetFileNameWithoutExtension(currentReportPath)), Encoding.UTF8), "project_name");
        form.Add(new StringContent(string.Empty, Encoding.UTF8), "department");
        form.Add(new StringContent("本期", Encoding.UTF8), "current_stage");
        form.Add(new StringContent((_llmReviewCheckBox?.IsChecked == true).ToString().ToLowerInvariant()), "use_llm");

        var streams = new List<FileStream>();
        try
        {
            foreach (var path in historyPaths)
            {
                var stream = File.OpenRead(path);
                streams.Add(stream);
                var content = new StreamContent(stream);
                content.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
                form.Add(content, "history_files", Path.GetFileName(path));
            }

            using var response = await BackendClient.PostAsync($"{BackendBaseUrl}/evaluate/duplicate/compare", form);
            var responseBody = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                throw new InvalidOperationException($"重复建设跨报告比对失败：{ExtractErrorMessage(responseBody)}");
            }

            return JsonDocument.Parse(responseBody);
        }
        finally
        {
            foreach (var stream in streams)
            {
                await stream.DisposeAsync();
            }
        }
    }

    private IEnumerable<string> SelectedRuleIdsForModule(string moduleCode)
    {
        if (!_ruleCheckBoxesByModule.TryGetValue(moduleCode, out var checkBoxes))
        {
            return BuiltInRuleModules.TryGetValue(moduleCode, out var builtInRules)
                ? builtInRules.Select(item => item.RuleId)
                : Enumerable.Empty<string>();
        }

        return checkBoxes
            .Where(item => item.IsChecked == true)
            .Select(item => item.Tag?.ToString())
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Cast<string>();
    }

    private void ResetResults()
    {
        ResultsSummaryPanel.Children.Clear();
        ResultsSummaryPanel.Children.Add(Text("检测结果", 17, FontWeights.SemiBold));
        ResultsListPanel.Children.Clear();
    }

    private void AddResultNotice(string message)
    {
        ResultsListPanel.Children.Add(Card(
            Text(message, 13, null, Brush(71, 84, 103), null, true),
            new Thickness(12),
            new Thickness(0, 0, 0, 10)));
    }

    private void RenderCheckResult(JsonDocument document)
    {
        var root = document.RootElement;
        var moduleCode = GetString(root, "module_code");
        var moduleName = GetString(root, "module_name");
        var status = GetString(root, "status");
        var elapsed = GetDouble(root, "elapsed_seconds");
        var summary = root.TryGetProperty("summary", out var summaryElement) ? summaryElement : default;
        var riskSummary = root.TryGetProperty("risk_summary", out var riskSummaryElement) ? riskSummaryElement : default;
        var ruleResultsSummary = root.TryGetProperty("rule_results_summary", out var ruleResultsSummaryElement) ? ruleResultsSummaryElement : default;
        var summarySource = ruleResultsSummary.ValueKind == JsonValueKind.Object
            ? ruleResultsSummary
            : summary.ValueKind == JsonValueKind.Object ? summary : riskSummary;
        var findingCount = GetInt(summarySource, "total_findings");
        var riskText = BuildRiskSummaryText(summarySource);

        ResultsSummaryPanel.Children.Add(Text($"{moduleName}：{status}", 14, FontWeights.SemiBold, FindBrush("TextBrush"), new Thickness(0, 8, 0, 0), true));
        ResultsSummaryPanel.Children.Add(Text($"问题数量：{findingCount}；耗时：{elapsed:0.###} 秒；{riskText}", 13, null, FindBrush("MutedBrush"), new Thickness(0, 3, 0, 0), true));

        var stack = new StackPanel();
        stack.Children.Add(Text(moduleName, 16, FontWeights.Bold, FindBrush("TextBrush")));
        stack.Children.Add(Text($"状态：{status}    问题数量：{findingCount}    耗时：{elapsed:0.###} 秒", 13, null, FindBrush("MutedBrush"), new Thickness(0, 5, 0, 10), true));
        RenderResultNotices(stack, root);

        if (moduleCode == "duplicate" && TryRenderDuplicateFindings(stack, root))
        {
            // 重复建设结果按内部重复和跨报告重复分组展示。
        }
        else if (TryRenderMergedRuleFindings(stack, root, moduleCode))
        {
            // 已合并展示小规则 findings，不再重复渲染顶层 findings。
        }
        else if (root.TryGetProperty("findings", out var findings) && findings.ValueKind == JsonValueKind.Array && findings.GetArrayLength() > 0)
        {
            foreach (var finding in findings.EnumerateArray())
            {
                stack.Children.Add(moduleCode == "sensitive_word"
                    ? BuildSensitiveFindingCard(finding)
                    : BuildFunctionFindingCard(finding));
            }
        }
        else
        {
            stack.Children.Add(Text("未发现需要复核的问题。", 13, null, Brush(2, 122, 72), null, true));
        }

        ResultsListPanel.Children.Add(Card(stack, new Thickness(14), new Thickness(0, 0, 0, 12)));
    }

    private void RenderResultNotices(Panel target, JsonElement root)
    {
        if (!root.TryGetProperty("notices", out var notices)
            || notices.ValueKind != JsonValueKind.Array
            || notices.GetArrayLength() == 0)
        {
            return;
        }

        foreach (var notice in notices.EnumerateArray())
        {
            var title = FirstNonEmpty(GetString(notice, "title"), "模块状态提示");
            var message = GetString(notice, "message");
            var action = GetString(notice, "action");
            target.Children.Add(Text(title, 14, FontWeights.SemiBold, Brush(181, 71, 8), new Thickness(0, 4, 0, 2), true));
            target.Children.Add(Text(message, 13, null, FindBrush("TextBrush"), null, true));
            if (!string.IsNullOrWhiteSpace(action))
            {
                target.Children.Add(Text(action, 13, null, FindBrush("MutedBrush"), new Thickness(0, 2, 0, 10), true));
            }
        }
    }

    private bool TryRenderMergedRuleFindings(Panel target, JsonElement root, string moduleCode)
    {
        if (moduleCode != "function_correspondence" && moduleCode != "basis" && moduleCode != "data_reasonableness")
        {
            return false;
        }

        if (!root.TryGetProperty("rule_results", out var ruleResults)
            || ruleResults.ValueKind != JsonValueKind.Array
            || ruleResults.GetArrayLength() == 0)
        {
            return false;
        }

        var renderedCount = 0;
        foreach (var ruleResult in ruleResults.EnumerateArray())
        {
            if (!ruleResult.TryGetProperty("findings", out var findings)
                || findings.ValueKind != JsonValueKind.Array
                || findings.GetArrayLength() == 0)
            {
                continue;
            }

            foreach (var finding in findings.EnumerateArray())
            {
                target.Children.Add(BuildFunctionFindingCard(finding));
                renderedCount++;
            }
        }

        if (renderedCount == 0)
        {
            target.Children.Add(Text("未发现需要复核的问题。", 13, null, Brush(2, 122, 72), null, true));
        }

        return true;
    }

    private bool TryRenderDuplicateFindings(Panel target, JsonElement root)
    {
        if (!root.TryGetProperty("findings", out var findings)
            || findings.ValueKind != JsonValueKind.Array)
        {
            return false;
        }

        var internalFindings = new List<JsonElement>();
        var crossFindings = new List<JsonElement>();
        foreach (var finding in findings.EnumerateArray())
        {
            var comparisonType = GetString(finding, "comparison_type");
            if (comparisonType == "cross_report")
            {
                crossFindings.Add(finding);
            }
            else
            {
                internalFindings.Add(finding);
            }
        }

        RenderDuplicateFindingGroup(target, "本报告内部重复", internalFindings, "未发现本报告内部重复功能点。");
        RenderDuplicateFindingGroup(target, "与往期报告重复", crossFindings, "未发现与往期报告重复的功能点。");
        return true;
    }

    private void RenderDuplicateFindingGroup(
        Panel target,
        string title,
        IReadOnlyCollection<JsonElement> findings,
        string emptyText)
    {
        target.Children.Add(Text($"{title}（{findings.Count}）", 15, FontWeights.Bold, FindBrush("TextBrush"), new Thickness(0, 10, 0, 8), true));
        if (findings.Count == 0)
        {
            target.Children.Add(Text(emptyText, 13, null, FindBrush("MutedBrush"), new Thickness(0, 0, 0, 10), true));
            return;
        }

        foreach (var finding in findings)
        {
            target.Children.Add(BuildFunctionFindingCard(finding));
        }
    }

    private Border BuildFunctionFindingCard(JsonElement finding)
    {
        return BuildBusinessFindingCard(finding);
    }

    private Border BuildSensitiveFindingCard(JsonElement finding)
    {
        return BuildBusinessFindingCard(finding);
    }

    private Border BuildBusinessFindingCard(JsonElement finding)
    {
        var title = FirstNonEmpty(GetString(finding, "display_title"), GetString(finding, "issue_type"), GetString(finding, "description"), GetString(finding, "hit_text"), "问题项");
        var opinion = FirstNonEmpty(GetString(finding, "review_opinion"), GetLlmReviewString(finding, "user_reason"), GetString(finding, "reason"));
        var evidence = FirstNonEmpty(GetString(finding, "evidence_summary"), GetLlmReviewString(finding, "user_basis"), GetString(finding, "evidence"), GetString(finding, "context"), GetString(finding, "source_section"));
        var location = FirstNonEmpty(GetString(finding, "location_hint"), GetString(finding, "source_section"));
        var advice = FirstNonEmpty(GetString(finding, "revision_advice"), GetLlmReviewString(finding, "user_suggestion"), GetString(finding, "suggestion"));
        var rule = FirstNonEmpty(GetString(finding, "rule_basis"), GetString(finding, "rule_name"), GetString(finding, "matched_rule"));
        var mergedCount = GetInt(finding, "merged_count");
        var llmRefined = GetBool(finding, "llm_refined");
        var findingSnapshot = finding.Clone();
        var stack = new StackPanel();
        stack.Children.Add(Text(title, 15, FontWeights.SemiBold, FindBrush("TextBrush"), null, true));
        stack.Children.Add(Text($"风险等级：{GetString(finding, "risk_level")}", 13, FontWeights.SemiBold, Brush(181, 71, 8), new Thickness(0, 5, 0, 0), true));
        stack.Children.Add(Text(llmRefined ? "AI辅助整理" : "规则审查结果", 12, FontWeights.SemiBold, llmRefined ? Brush(37, 99, 235) : Brush(102, 112, 133), new Thickness(0, 5, 0, 0), true));
        AddTextIf(stack, "审查意见", opinion, FindBrush("TextBrush"));
        if (UsesCompactReportEvidence(finding))
        {
            AddCompactDocumentBasis(stack, evidence, finding);
            AddRevisionAdvice(stack, advice, string.Empty);
            AddCompactReportEvidence(stack, finding);
        }
        else
        {
        AddTextIf(stack, "文档依据", evidence, FindBrush("MutedBrush"));
        AddRevisionAdvice(stack, advice, location);
        AddSourceHighlights(stack, finding);
        if (string.Equals(GetString(finding, "comparison_type"), "cross_report", StringComparison.OrdinalIgnoreCase))
        {
            var sourceButtons = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 8, 0, 0) };
            var currentButton = Button("定位当前报告", false);
            currentButton.Margin = new Thickness(0, 0, 8, 0);
            currentButton.Click += async (_, e) =>
            {
                e.Handled = true;
                await NavigateToFindingAsync(findingSnapshot, "current");
            };
            var relatedButton = Button("定位关联报告", false);
            relatedButton.Click += async (_, e) =>
            {
                e.Handled = true;
                await NavigateToFindingAsync(findingSnapshot, "related");
            };
            sourceButtons.Children.Add(currentButton);
            sourceButtons.Children.Add(relatedButton);
            stack.Children.Add(sourceButtons);
        }
        }
        AddTextIf(stack, "涉及规则", rule, FindBrush("MutedBrush"));
        if (mergedCount > 1)
        {
            stack.Children.Add(Text($"同类问题数量：{mergedCount}", 12, null, FindBrush("MutedBrush"), new Thickness(0, 3, 0, 0), true));
        }
        var card = new Border
        {
            Background = Brush(248, 250, 252),
            BorderBrush = Brush(234, 236, 240),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(12),
            Margin = new Thickness(0, 0, 0, 10),
            Child = stack,
            Cursor = Cursors.Hand,
            ToolTip = "在右侧原文中定位此问题"
        };
        card.MouseLeftButtonUp += async (_, _) => await NavigateToFindingAsync(findingSnapshot);
        return card;
    }

    // Only the five rules opt in. Other modules retain their existing cards.
    private static bool UsesCompactReportEvidence(JsonElement finding) => GetString(finding, "rule_id") is
        "R15_SECURITY_PAAS_CRYPTO_QUANTITY" or "R16_SERVER_OS_QUANTITY" or "R16_DB_SERVER_DATABASE_QUANTITY"
        or "DATA_REASON_001" or "DATA_REASON_002" or "DATA_REASON_024";

    private static string EvidencePreview(string value, int limit = 180) =>
        value.Length <= limit ? value : value[..limit] + "…";

    private TextBox SelectableEvidence(string text) => new()
    {
        Text = text, IsReadOnly = true, TextWrapping = TextWrapping.Wrap, AcceptsReturn = true,
        BorderThickness = new Thickness(0), Background = Brushes.Transparent,
        Foreground = FindBrush("TextBrush"), FontSize = 13, Padding = new Thickness(0),
        Margin = new Thickness(0, 5, 0, 3), HorizontalAlignment = HorizontalAlignment.Stretch
    };

    private void AddCompactDocumentBasis(StackPanel panel, string evidence, JsonElement finding)
    {
        if (string.IsNullOrWhiteSpace(evidence)) return;
        panel.Children.Add(Text("文档依据", 13, FontWeights.SemiBold, FindBrush("TextBrush"), new Thickness(0, 8, 0, 0)));
        // Show the actual basis, replacing internal table addresses with the
        // report's own title where available. Do not replace it with a pointer.
        var sources = ReportEvidenceSources(finding);
        var readable = System.Text.RegularExpressions.Regex.Replace(evidence, @"DOCX表格(\d+)(?:行(\d+))?", match =>
        {
            var source = sources.FirstOrDefault(x => GetInt(x, "table_index").ToString() == match.Groups[1].Value);
            var caption = GetString(source, "table_caption");
            if (string.IsNullOrWhiteSpace(caption)) return match.Value;
            return caption + (match.Groups[2].Success ? $"，表内第{match.Groups[2].Value}行" : "");
        });
        var points = readable.Split(new[] { '；', '\n' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).Distinct().ToList();
        var shown = string.Join("\n", points.Select(x => points.Count > 1 ? "• " + x : x));
        panel.Children.Add(SelectableEvidence(EvidencePreview(shown, 1200)));
        if (shown.Length > 1200) panel.Children.Add(new Expander { Header = "查看完整文档依据", IsExpanded = false,
            Margin = new Thickness(0, 5, 0, 0), Content = SelectableEvidence(shown) });
    }

    private static List<JsonElement> ReportEvidenceSources(JsonElement finding)
    {
        var sources = new List<JsonElement>();
        if (finding.TryGetProperty("source_locations", out var locations) && locations.ValueKind == JsonValueKind.Array)
            sources.AddRange(locations.EnumerateArray());
        if (sources.Count == 0 && finding.TryGetProperty("source_location", out var location) && location.ValueKind == JsonValueKind.Object
            && location.TryGetProperty("entries", out var entries) && entries.ValueKind == JsonValueKind.Array)
            sources.AddRange(entries.EnumerateArray());
        if (sources.Count == 0 && finding.TryGetProperty("source_highlights", out var highlights) && highlights.ValueKind == JsonValueKind.Array)
            sources.AddRange(highlights.EnumerateArray());
        return sources.Where(x => x.ValueKind == JsonValueKind.Object && !string.IsNullOrWhiteSpace(GetString(x, "quote")))
            .DistinctBy(x => (EvidenceGroupKey(x), GetInt(x, "row_index"), GetInt(x, "paragraph"),
                GetInt(x, "line_start"), GetInt(x, "column_start"), GetString(x, "quote"))).ToList();
    }

    private static string EvidenceGroupKey(JsonElement source) => string.Join("\u001f", new[] {
        GetString(source, "file_name"), GetString(source, "section"), GetString(source, "table_index"),
        GetString(source, "table_path"), GetString(source, "sheet_name"), GetString(source, "page"),
        GetString(source, "role") == "scope" ? "scope" : "evidence" });

    private static string EvidenceRows(IEnumerable<JsonElement> sources)
    {
        var numbers = sources.Select(x => GetInt(x, "row_index")).Where(x => x > 0).Distinct().Order().ToArray();
        var ranges = new List<string>();
        for (var i = 0; i < numbers.Length; i++)
        {
            var start = numbers[i]; var end = start;
            while (i + 1 < numbers.Length && numbers[i + 1] == end + 1) end = numbers[++i];
            ranges.Add(start == end ? start.ToString() : $"{start}—{end}");
        }
        return ranges.Count == 0 ? "" : "表内第 " + string.Join("、", ranges) + " 行（含表头，非序号列）";
    }

    private void AddCompactReportEvidence(StackPanel panel, JsonElement finding)
    {
        panel.Children.Add(Text("报告原文", 14, FontWeights.SemiBold, FindBrush("TextBrush"), new Thickness(0, 12, 0, 4)));
        var sources = ReportEvidenceSources(finding);
        if (sources.Count == 0)
        {
            panel.Children.Add(Text("尚无可核验的具体原文位置，请按核查范围人工查找。", 12, null, FindBrush("MutedBrush"), null, true));
            var hint = FirstNonEmpty(GetString(finding, "location_hint"), GetString(finding, "source_section"));
            if (!string.IsNullOrWhiteSpace(hint)) panel.Children.Add(SelectableEvidence(hint));
            return;
        }
        var groups = sources.GroupBy(EvidenceGroupKey).Select(x => x.ToList()).ToList();
        panel.Children.Add(Text("以下按报告章节和表格列出对应原文。表内行号含表头，不等同于序号列。", 12, null, FindBrush("MutedBrush"), null, true));
        var ambiguous = sources.Any(x => GetString(x, "match_status") == "ambiguous");
        if (ambiguous) panel.Children.Add(Text("原文存在多处匹配：以下为候选位置，需要结合章节和表名确认。", 12, FontWeights.SemiBold, Brush(181, 71, 8), null, true));
        for (var i = 0; i < groups.Count; i++) panel.Children.Add(BuildReportEvidenceGroup(groups[i], i + 1));
    }

    private Border BuildReportEvidenceGroup(List<JsonElement> sources, int number)
    {
        var first = sources[0];
        var section = GetString(first, "section");
        var leaf = section.Split('→', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries).LastOrDefault() ?? "未识别章节标题";
        var caption = GetString(first, "table_caption");
        var sheet = GetString(first, "sheet_name");
        var scope = GetString(first, "role") == "scope" || GetString(first, "match_status") == "scope";
        var body = new StackPanel();
        body.Children.Add(Text($"{number}. {leaf}" + (scope ? "（核查范围）" : ""), 13, FontWeights.SemiBold, FindBrush("TextBrush"), null, true));
        if (section != leaf && !string.IsNullOrWhiteSpace(section)) body.Children.Add(Text(section, 12, null, FindBrush("MutedBrush"), new Thickness(0, 3, 0, 3), true));
        if (!string.IsNullOrWhiteSpace(caption)) body.Children.Add(Text(caption, 13, FontWeights.SemiBold, Brush(146, 64, 14), null, true));
        else if (!string.IsNullOrWhiteSpace(sheet)) body.Children.Add(Text(sheet + (GetInt(first, "table_index") > 0 ? "（程序解析编号，以报告章节为准）" : ""), 12, null, FindBrush("MutedBrush"), null, true));
        var rows = EvidenceRows(sources);
        if (!string.IsNullOrEmpty(rows)) body.Children.Add(Text(rows, 12, null, FindBrush("MutedBrush"), null, true));
        if (GetInt(first, "page") > 0) body.Children.Add(Text($"第{GetInt(first, "page")}页", 12, null, FindBrush("MutedBrush")));
        if (scope) body.Children.Add(Text("这里只标明已核查的范围，不代表此处文字本身有错误。", 12, null, FindBrush("MutedBrush"), null, true));
        var filename = GetString(first, "file_name");
        if (!string.IsNullOrWhiteSpace(filename)) body.Children.Add(Text("文件：" + filename, 11, null, FindBrush("MutedBrush"), null, true));
        if (!string.IsNullOrWhiteSpace(GetString(first, "table_path"))) body.Children.Add(Text("嵌套表位置：" + GetString(first, "table_path"), 12, null, FindBrush("MutedBrush"), null, true));
        // Small comparisons stay fully visible. Large aggregate tables retain
        // several representative rows without repeating the heading 40 times.
        var visibleCount = sources.Count <= 6 ? sources.Count : 4;
        for (var i = 0; i < visibleCount; i++) body.Children.Add(BuildReportExcerpt(sources[i]));
        if (sources.Count > visibleCount)
        {
            var rest = new StackPanel();
            foreach (var source in sources.Skip(visibleCount)) rest.Children.Add(BuildReportExcerpt(source));
            body.Children.Add(Text($"该表共涉及{sources.Count}处原文，以上展示{visibleCount}处；汇总数量的核验还涉及下方其余行。", 12, null, FindBrush("MutedBrush"), null, true));
            body.Children.Add(new Expander { Header = $"查看其余 {sources.Count - visibleCount} 处原文", Content = rest });
        }
        return new Border { Background = Brush(255, 251, 235), BorderBrush = Brush(245, 218, 148), BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(6), Padding = new Thickness(10), Margin = new Thickness(0, 6, 0, 4), Child = body };
    }

    private StackPanel BuildReportExcerpt(JsonElement source)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 5, 0, 7) };
        var row = GetInt(source, "row_index"); var paragraph = GetInt(source, "paragraph"); var column = GetInt(source, "column_start");
        var address = row > 0 ? $"表内第{row}行" : paragraph > 0 ? $"正文第{paragraph}段（解析顺序）" : "原文摘录";
        if (column > 0) address += $"，第{column}列";
        panel.Children.Add(Text(address, 12, FontWeights.SemiBold, Brush(146, 64, 14)));
        var quote = GetString(source, "quote");
        panel.Children.Add(SelectableEvidence(EvidencePreview(quote, 900)));
        if (quote.Length > 900) panel.Children.Add(new Expander { Header = "展开本处完整摘录", Content = SelectableEvidence(quote) });
        return panel;
    }

    private void AddRevisionAdvice(StackPanel stack, string advice, string location)
    {
        if (string.IsNullOrWhiteSpace(advice) && string.IsNullOrWhiteSpace(location))
        {
            return;
        }

        var content = new StackPanel();
        content.Children.Add(Text("修改建议", 13, FontWeights.SemiBold, Brush(29, 78, 216)));
        if (!string.IsNullOrWhiteSpace(advice))
        {
            content.Children.Add(Text(advice, 13, null, FindBrush("TextBrush"), new Thickness(0, 4, 0, 0), true));
        }
        if (!string.IsNullOrWhiteSpace(location))
        {
            content.Children.Add(Text($"修改位置：{location}", 12, FontWeights.SemiBold, Brush(71, 84, 103), new Thickness(0, 5, 0, 0), true));
        }
        stack.Children.Add(new Border
        {
            Background = Brush(239, 246, 255),
            BorderBrush = Brush(147, 197, 253),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(6),
            Padding = new Thickness(10),
            Margin = new Thickness(0, 8, 0, 0),
            Child = content
        });
    }

    private void AddSourceHighlights(StackPanel stack, JsonElement finding)
    {
        if (!finding.TryGetProperty("source_highlights", out var highlights)
            || highlights.ValueKind != JsonValueKind.Array)
        {
            AddLegacySourceHighlight(stack, "当前报告原文", GetString(finding, "item_source_excerpt"), GetString(finding, "location_hint"));
            AddLegacySourceHighlight(stack, "关联报告原文", GetString(finding, "related_source_excerpt"), string.Empty);
            return;
        }

        foreach (var highlight in highlights.EnumerateArray())
        {
            var quote = GetString(highlight, "quote");
            if (string.IsNullOrWhiteSpace(quote))
            {
                continue;
            }

            var role = GetString(highlight, "role") switch
            {
                "current" => "当前报告原文",
                "related" => "关联报告原文",
                _ => "定位原文"
            };
            var locator = FormatHighlightLocation(highlight);
            AddLegacySourceHighlight(stack, role, quote, locator);
        }
    }

    private void AddLegacySourceHighlight(StackPanel stack, string role, string quote, string locator)
    {
        if (string.IsNullOrWhiteSpace(quote))
        {
            return;
        }
        var content = new StackPanel();
        content.Children.Add(Text(
            string.IsNullOrWhiteSpace(locator) ? role : $"{role} · {locator}",
            12,
            FontWeights.SemiBold,
            Brush(146, 64, 14),
            null,
            true));
        content.Children.Add(Text(quote, 13, null, Brush(66, 32, 6), new Thickness(0, 4, 0, 0), true));
        stack.Children.Add(new Border
        {
            Background = Brush(255, 247, 214),
            BorderBrush = Brush(245, 196, 81),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(6),
            Padding = new Thickness(10),
            Margin = new Thickness(0, 6, 0, 0),
            Child = content
        });
    }

    private static string FormatHighlightLocation(JsonElement highlight)
    {
        var filename = GetString(highlight, "file_name");
        var sheet = GetString(highlight, "sheet_name");
        var page = GetInt(highlight, "page");
        var line = GetInt(highlight, "line_start");
        var row = GetInt(highlight, "row_index");
        var paragraph = GetInt(highlight, "paragraph");
        var section = GetString(highlight, "section");
        var parts = new List<string>();
        if (!string.IsNullOrWhiteSpace(filename)) parts.Add(filename);
        if (!string.IsNullOrWhiteSpace(sheet)) parts.Add(sheet);
        if (page > 0) parts.Add($"第{page}页");
        if (line > 0) parts.Add($"第{line}行");
        else if (row > 0) parts.Add($"第{row}行");
        else if (paragraph > 0) parts.Add($"第{paragraph}段");
        if (!string.IsNullOrWhiteSpace(section)) parts.Add(section);
        return string.Join(" · ", parts);
    }

    private void AddTextIf(StackPanel stack, string label, string value, Brush brush)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return;
        }

        stack.Children.Add(Text($"{label}：{value}", 13, null, brush, new Thickness(0, 5, 0, 0), true));
    }

    private void AddLlmBadge(StackPanel stack, JsonElement finding)
    {
        if (!HasLlmRefinedText(finding))
        {
            return;
        }

        stack.Children.Add(Text("大模型辅助整理", 12, FontWeights.SemiBold, Brush(37, 99, 235), new Thickness(0, 5, 0, 0), true));
    }

    private void AddLlmReviewText(StackPanel stack, JsonElement finding)
    {
        if (!finding.TryGetProperty("llm_review", out var review) || review.ValueKind != JsonValueKind.Object)
        {
            return;
        }

        var judgement = GetString(review, "judgement");
        var risk = FirstNonEmpty(GetString(finding, "llm_risk_level_suggestion"), GetString(review, "risk_level_suggestion"));
        var reason = FirstNonEmpty(GetString(review, "user_reason"), GetString(review, "reason"));
        var rewrite = FirstNonEmpty(GetString(review, "user_suggestion"), GetString(review, "rewrite_suggestion"));
        stack.Children.Add(Text($"大模型复核：{judgement}｜风险建议：{risk}", 12, FontWeights.SemiBold, Brush(37, 99, 235), new Thickness(0, 6, 0, 0), true));
        if (!string.IsNullOrWhiteSpace(reason))
        {
            stack.Children.Add(Text($"复核理由：{reason}", 12, null, FindBrush("MutedBrush"), new Thickness(0, 3, 0, 0), true));
        }
        if (!string.IsNullOrWhiteSpace(rewrite))
        {
            stack.Children.Add(Text($"改写建议：{rewrite}", 12, null, FindBrush("MutedBrush"), new Thickness(0, 3, 0, 0), true));
        }
    }

    private static bool HasLlmRefinedText(JsonElement finding) =>
        !string.IsNullOrWhiteSpace(GetLlmReviewString(finding, "user_reason"))
        || !string.IsNullOrWhiteSpace(GetLlmReviewString(finding, "user_suggestion"))
        || !string.IsNullOrWhiteSpace(GetLlmReviewString(finding, "user_basis"));

    private static string GetLlmReviewString(JsonElement finding, string propertyName)
    {
        if (finding.ValueKind == JsonValueKind.Object
            && finding.TryGetProperty("llm_review", out var review)
            && review.ValueKind == JsonValueKind.Object)
        {
            return GetString(review, propertyName);
        }

        return string.Empty;
    }

    private static string BuildRiskSummaryText(JsonElement summary)
    {
        if (summary.ValueKind != JsonValueKind.Object)
        {
            return "风险汇总：无";
        }

        if (summary.TryGetProperty("risk_count", out var riskCount))
        {
            return $"风险汇总：{JsonObjectToText(riskCount)}";
        }

        if (summary.TryGetProperty("risk_distribution", out var riskDistribution))
        {
            return $"风险汇总：{JsonObjectToText(riskDistribution)}";
        }

        return "风险汇总：无";
    }

    private static string LlmModelText(JsonElement summary)
    {
        var model = GetString(summary, "llm_model");
        return string.IsNullOrWhiteSpace(model) ? string.Empty : $"模型：{model}";
    }

    private static string JsonObjectToText(JsonElement element)
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return "无";
        }

        var values = element.EnumerateObject()
            .Select(item => $"{item.Name} {item.Value}")
            .ToList();
        return values.Count == 0 ? "无" : string.Join("，", values);
    }

    private static string ExtractErrorMessage(string responseBody)
    {
        try
        {
            using var document = JsonDocument.Parse(responseBody);
            return GetString(document.RootElement, "detail");
        }
        catch
        {
            return string.IsNullOrWhiteSpace(responseBody) ? "后端未返回错误详情" : responseBody;
        }
    }

    private static string GetString(JsonElement element, string propertyName)
    {
        if (element.ValueKind == JsonValueKind.Object && element.TryGetProperty(propertyName, out var value))
        {
            return value.ValueKind == JsonValueKind.String ? value.GetString() ?? string.Empty : value.ToString();
        }

        return string.Empty;
    }

    private static int GetInt(JsonElement element, string propertyName)
    {
        if (element.ValueKind == JsonValueKind.Object && element.TryGetProperty(propertyName, out var value))
        {
            if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
            {
                return number;
            }

            if (int.TryParse(value.ToString(), out number))
            {
                return number;
            }
        }

        return 0;
    }

    private static bool GetBool(JsonElement element, string propertyName)
    {
        if (element.ValueKind == JsonValueKind.Object && element.TryGetProperty(propertyName, out var value))
        {
            if (value.ValueKind == JsonValueKind.True)
            {
                return true;
            }
            if (value.ValueKind == JsonValueKind.False)
            {
                return false;
            }
            if (bool.TryParse(value.ToString(), out var result))
            {
                return result;
            }
        }

        return false;
    }

    private static double GetDouble(JsonElement element, string propertyName)
    {
        if (element.ValueKind == JsonValueKind.Object && element.TryGetProperty(propertyName, out var value))
        {
            if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
            {
                return number;
            }

            if (double.TryParse(value.ToString(), out number))
            {
                return number;
            }
        }

        return 0;
    }

    private static string FormatDateTime(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "未记录时间";
        }

        // SQLAlchemy stores UTC values without an offset. Treat offset-less
        // timestamps as UTC, while preserving explicit offsets from newer API
        // responses, then display in the user's local timezone.
        if (DateTimeOffset.TryParse(
                value,
                CultureInfo.InvariantCulture,
                DateTimeStyles.AllowWhiteSpaces | DateTimeStyles.AssumeUniversal,
                out var parsed))
        {
            return parsed.ToLocalTime().ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture);
        }

        return "未记录时间";
    }

    private static string FirstNonEmpty(params string[] values) =>
        values.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value)) ?? string.Empty;
}

