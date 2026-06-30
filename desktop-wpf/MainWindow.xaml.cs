using Microsoft.Win32;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace AiReportDesktop;

public partial class MainWindow : Window
{
    private readonly List<CheckBox> _checkBoxes = new();

    private readonly (string Title, string Description, string Badge)[] _checkItems =
    {
        ("建设依据审查", "根据上传的政策依据附件，结合可研中的依据表述，审查依据、附件内容与建设内容是否一致。", "依据"),
        ("重复建设检查", "筛查可研自身重复功能点，并预留历史批复功能点清单比对入口。", "重复"),
        ("建设功能的对应关系检查", "检查建设内容、业务功能分析、系统功能需求分析、应用功能设计、功能点清单之间的对应关系。", "对应"),
        ("数据填报合理性", "检查用户量、业务量、并发数、高峰期用户、活跃用户比例、数据量、存储量和计算资源依据。", "数据"),
        ("资源申请的合理性", "检查云资源和三大件资源申请是否与系统功能、数据规模和性能需求匹配。", "资源"),
        ("安全内容的合理性", "检查安全需求分析与项目建设内容、安全建设内容之间的匹配性。", "安全"),
        ("价格合理性", "根据软件造价标准拆解功能点，预留基于标准的核价结果入口。", "造价"),
        ("软硬件产品价格参考", "预留政采网、住建部询价网和爬虫价格数据的参考价展示入口。", "询价"),
        ("非信创内容检查", "检查可研中是否采用非信创技术、产品或不符合国产化要求的内容。", "信创")
    };

    public MainWindow()
    {
        InitializeComponent();
        BuildCheckItems();
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
                Child = new TextBlock
                {
                    Text = item.Badge,
                    Foreground = new SolidColorBrush(Color.FromRgb(29, 78, 216)),
                    FontWeight = FontWeights.Bold
                }
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
            stack.Children.Add(new TextBlock
            {
                Text = item.Description,
                Foreground = new SolidColorBrush(Color.FromRgb(102, 112, 133)),
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 12, 0, 0),
                LineHeight = 22
            });

            border.Child = stack;
            CheckItemsGrid.Children.Add(border);
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

        var selectedCount = _checkBoxes.Count(item => item.IsChecked == true);
        if (selectedCount == 0)
        {
            MessageBox.Show(this, "请至少选择一个检测项。", "缺少检测项", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        StatusTextBlock.Text = $"已创建检测任务：{selectedCount} 项。后端接入后将在此展示进度和结果。";
    }
}
