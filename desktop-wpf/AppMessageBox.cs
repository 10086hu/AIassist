using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace AiReportDesktop;

internal static class AppMessageBox
{
    public static MessageBoxResult Show(Window owner, string message, string caption = "提示",
        MessageBoxButton buttons = MessageBoxButton.OK, MessageBoxImage image = MessageBoxImage.None)
    {
        var result = CloseResult(buttons);
        var dialog = Build(owner, message, caption, buttons, image, choice => result = choice);
        dialog.ShowDialog();
        return result;
    }

    internal static AppDialog Build(Window owner, string message, string caption, MessageBoxButton buttons,
        MessageBoxImage image, Action<MessageBoxResult> choose)
    {
        var icon = image switch
        {
            MessageBoxImage.Warning => "!",
            MessageBoxImage.Error => "!",
            MessageBoxImage.Question => "?",
            _ => "i",
        };
        var dialog = new AppDialog(caption, "", icon) { Owner = owner, CanDismiss = buttons != MessageBoxButton.YesNo };
        dialog.DialogSubtitle.Visibility = Visibility.Collapsed;
        dialog.DialogBody.Content = new TextBlock
        {
            Text = message,
            TextWrapping = TextWrapping.Wrap,
            LineHeight = 24,
            Foreground = new SolidColorBrush(Color.FromRgb(52, 64, 84)),
        };
        var footer = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var choices = buttons switch
        {
            MessageBoxButton.OK => new[] { ("确定", MessageBoxResult.OK) },
            MessageBoxButton.OKCancel => new[] { ("取消", MessageBoxResult.Cancel), ("确定", MessageBoxResult.OK) },
            MessageBoxButton.YesNo => new[] { ("否", MessageBoxResult.No), ("是", MessageBoxResult.Yes) },
            MessageBoxButton.YesNoCancel => new[] { ("取消", MessageBoxResult.Cancel), ("否", MessageBoxResult.No), ("是", MessageBoxResult.Yes) },
            _ => throw new ArgumentOutOfRangeException(nameof(buttons)),
        };
        for (var i = 0; i < choices.Length; i++)
        {
            var (label, result) = choices[i];
            var primary = result is MessageBoxResult.OK or MessageBoxResult.Yes;
            var button = new Button
            {
                Content = label,
                Margin = new Thickness(i == 0 ? 0 : 12, 0, 0, 0),
                Style = (Style)dialog.FindResource(primary ? "DialogPrimaryButton" : "DialogSecondaryButton"),
                IsDefault = primary,
                IsCancel = result == MessageBoxResult.Cancel || buttons == MessageBoxButton.OK,
            };
            button.Click += (_, _) =>
            {
                choose(result);
                dialog.CanDismiss = true;
                dialog.DialogResult = true;
            };
            footer.Children.Add(button);
        }
        dialog.DialogFooter.Content = footer;
        return dialog;
    }

    private static MessageBoxResult CloseResult(MessageBoxButton buttons) => buttons switch
    {
        MessageBoxButton.OK => MessageBoxResult.OK,
        MessageBoxButton.YesNo => MessageBoxResult.No,
        _ => MessageBoxResult.Cancel,
    };
}
