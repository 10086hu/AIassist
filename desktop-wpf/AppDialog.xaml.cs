using System.Windows;
using System.Windows.Input;

namespace AiReportDesktop;

public partial class AppDialog : Window
{
    private bool _canDismiss = true;

    public bool CanDismiss
    {
        get => _canDismiss;
        set { _canDismiss = value; DialogClose.IsEnabled = value; }
    }

    public AppDialog(string title, string subtitle, string icon)
    {
        InitializeComponent();
        Title = title;
        DialogHeading.Text = title;
        DialogSubtitle.Text = subtitle;
        DialogIcon.Text = icon;
        MaxWidth = Math.Max(320, SystemParameters.WorkArea.Width - 32);
        MaxHeight = Math.Max(240, SystemParameters.WorkArea.Height - 32);
        Closing += (_, e) => e.Cancel = !CanDismiss;
    }

    private void OnHeaderMouseDown(object sender, MouseButtonEventArgs e)
    {
        if (e.ButtonState == MouseButtonState.Pressed)
        {
            DragMove();
        }
    }

    private void OnCloseClicked(object sender, RoutedEventArgs e) => Close();

    private void OnDialogKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape && CanDismiss)
        {
            Close();
            e.Handled = true;
        }
    }
}
