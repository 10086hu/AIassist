using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace AiReportDesktop;

public partial class MainWindow
{
    private AppDialog BuildDuplicateHistoryDialog(Action<MessageBoxResult> choose)
    {
        var dialog = new AppDialog(
            "重复建设检查",
            "是否将往期可研报告、历史批复报告或历史功能点清单一起比对？",
            "⇄") { Owner = this };
        var options = new StackPanel();
        options.Children.Add(BuildHistoryChoice(
            dialog, "是，比对往期报告", "选择往期可研报告、历史批复报告或历史功能点清单，与本次报告一起比对。",
            "01", MessageBoxResult.Yes, choose));
        options.Children.Add(BuildHistoryChoice(
            dialog, "否，仅检查本报告", "不添加往期文件，按现有规则检查当前报告的重复建设问题。",
            "02", MessageBoxResult.No, choose));
        dialog.DialogBody.Content = options;

        var cancel = new Button
        {
            Content = "取消检查",
            Style = (Style)dialog.FindResource("DialogSecondaryButton"),
            HorizontalAlignment = HorizontalAlignment.Right,
            IsCancel = true,
        };
        cancel.Click += (_, _) => dialog.Close();
        dialog.DialogFooter.Content = cancel;
        return dialog;
    }

    private Button BuildHistoryChoice(AppDialog dialog, string title, string description, string number,
        MessageBoxResult result, Action<MessageBoxResult> choose)
    {
        var content = new Grid();
        content.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        content.ColumnDefinitions.Add(new ColumnDefinition());
        content.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var badge = new Border
        {
            Width = 36,
            Height = 36,
            Background = new SolidColorBrush(Color.FromRgb(239, 246, 255)),
            CornerRadius = new CornerRadius(9),
            Margin = new Thickness(0, 2, 14, 0),
            VerticalAlignment = VerticalAlignment.Top,
            Child = new TextBlock
            {
                Text = number,
                Foreground = FindBrush("PrimaryBrush"),
                FontWeight = FontWeights.SemiBold,
                HorizontalAlignment = HorizontalAlignment.Center,
                VerticalAlignment = VerticalAlignment.Center,
            },
        };
        content.Children.Add(badge);
        var text = new StackPanel();
        text.Children.Add(Text(title, 15, FontWeights.SemiBold, FindBrush("TextBrush")));
        var detail = Text(description, 13, FontWeights.Normal, FindBrush("MutedBrush"), new Thickness(0, 7, 0, 0), true);
        detail.LineHeight = 21;
        text.Children.Add(detail);
        Grid.SetColumn(text, 1);
        content.Children.Add(text);
        var arrow = Text("›", 24, FontWeights.Normal, FindBrush("MutedBrush"), new Thickness(14, 0, 0, 0));
        arrow.VerticalAlignment = VerticalAlignment.Center;
        Grid.SetColumn(arrow, 2);
        content.Children.Add(arrow);
        var button = new Button
        {
            Content = content,
            Style = (Style)dialog.FindResource("DialogChoiceButton"),
            IsDefault = result == MessageBoxResult.Yes,
        };
        System.Windows.Automation.AutomationProperties.SetName(button, title);
        button.Click += (_, _) =>
        {
            choose(result);
            dialog.DialogResult = true;
        };
        return button;
    }

    private MessageBoxResult ShowDuplicateHistoryDialog()
    {
        var answer = MessageBoxResult.Cancel;
        var dialog = BuildDuplicateHistoryDialog(result => answer = result);
        dialog.ShowDialog();
        return answer;
    }
}
