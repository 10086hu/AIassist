using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Shell;
using Path = System.Windows.Shapes.Path;

namespace AiReportDesktop;

// Uses the Windows resize/drag behavior while drawing the frame in the app's colors.
internal static class DesktopWindowFrame
{
    public static void Apply(Window window)
    {
        var content = window.Content;
        window.Content = null;
        window.WindowStyle = WindowStyle.None;
        WindowChrome.SetWindowChrome(window, new WindowChrome
        {
            CaptionHeight = 52,
            ResizeBorderThickness = new Thickness(6),
            GlassFrameThickness = new Thickness(0),
            CornerRadius = new CornerRadius(0),
            UseAeroCaptionButtons = false,
        });

        var frame = new Border
        {
            BorderBrush = new SolidColorBrush(Color.FromRgb(216, 222, 233)),
            BorderThickness = new Thickness(1),
            Background = new SolidColorBrush(Color.FromRgb(238, 242, 248)),
        };
        var layout = new Grid();
        layout.RowDefinitions.Add(new RowDefinition { Height = new GridLength(52) });
        layout.RowDefinitions.Add(new RowDefinition());
        var header = new Grid { Background = Brushes.White };
        header.ColumnDefinitions.Add(new ColumnDefinition());
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var title = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(18, 0, 0, 0) };
        title.Children.Add(new Border
        {
            Width = 30,
            Height = 30,
            CornerRadius = new CornerRadius(8),
            Background = new SolidColorBrush(Color.FromRgb(37, 99, 235)),
            Child = new TextBlock { Text = "AI", FontSize = 12, FontWeight = FontWeights.Bold, Foreground = Brushes.White, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center },
        });
        var titleText = new TextBlock
        {
            FontSize = 14,
            FontWeight = FontWeights.SemiBold,
            Foreground = new SolidColorBrush(Color.FromRgb(16, 24, 40)),
            VerticalAlignment = VerticalAlignment.Center,
            Margin = new Thickness(12, 0, 0, 0),
        };
        titleText.SetBinding(TextBlock.TextProperty, new System.Windows.Data.Binding(nameof(Window.Title)) { Source = window });
        title.Children.Add(titleText);
        header.Children.Add(title);

        var controls = new StackPanel { Orientation = Orientation.Horizontal };
        WindowChrome.SetIsHitTestVisibleInChrome(controls, true);
        var minimize = CaptionButton("最小化", "M 0,5 L 12,5", false);
        minimize.Click += (_, _) => SystemCommands.MinimizeWindow(window);
        controls.Children.Add(minimize);
        var maximize = CaptionButton("最大化", "M 0,0 L 12,0 L 12,12 L 0,12 Z", false);
        maximize.Click += (_, _) =>
        {
            if (window.WindowState == WindowState.Maximized) SystemCommands.RestoreWindow(window);
            else SystemCommands.MaximizeWindow(window);
        };
        controls.Children.Add(maximize);
        var close = CaptionButton("关闭", "M 0,0 L 12,12 M 12,0 L 0,12", true);
        close.Click += (_, _) => window.Close();
        controls.Children.Add(close);
        void UpdateState()
        {
            var maximized = window.WindowState == WindowState.Maximized;
            frame.BorderThickness = new Thickness(maximized ? 0 : 1);
            maximize.ToolTip = maximized ? "还原" : "最大化";
            System.Windows.Automation.AutomationProperties.SetName(maximize, maximized ? "还原" : "最大化");
            ((Path)maximize.Content).Data = Geometry.Parse(maximized
                ? "M 3,0 L 12,0 L 12,9 M 0,3 L 9,3 L 9,12 L 0,12 Z"
                : "M 0,0 L 12,0 L 12,12 L 0,12 Z");
            maximize.IsEnabled = window.ResizeMode is ResizeMode.CanResize or ResizeMode.CanResizeWithGrip;
            minimize.IsEnabled = window.ResizeMode != ResizeMode.NoResize;
        }
        window.StateChanged += (_, _) => UpdateState();
        UpdateState();
        Grid.SetColumn(controls, 1);
        header.Children.Add(controls);
        layout.Children.Add(new Border { Child = header, BorderBrush = new SolidColorBrush(Color.FromRgb(228, 231, 236)), BorderThickness = new Thickness(0, 0, 0, 1) });
        var body = new ContentControl { Content = content, HorizontalContentAlignment = HorizontalAlignment.Stretch, VerticalContentAlignment = VerticalAlignment.Stretch };
        Grid.SetRow(body, 1);
        layout.Children.Add(body);
        frame.Child = layout;
        window.Content = frame;
    }

    private static Button CaptionButton(string label, string geometry, bool close)
    {
        var border = new FrameworkElementFactory(typeof(Border));
        border.SetValue(Border.BackgroundProperty, new TemplateBindingExtension(Control.BackgroundProperty));
        var presenter = new FrameworkElementFactory(typeof(ContentPresenter));
        presenter.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Center);
        presenter.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center);
        border.AppendChild(presenter);
        var style = new Style(typeof(Button));
        style.Setters.Add(new Setter(Control.TemplateProperty, new ControlTemplate(typeof(Button)) { VisualTree = border }));
        var hover = new Trigger { Property = UIElement.IsMouseOverProperty, Value = true };
        hover.Setters.Add(new Setter(Control.BackgroundProperty, new SolidColorBrush(close ? Color.FromRgb(254, 242, 242) : Color.FromRgb(239, 246, 255))));
        style.Triggers.Add(hover);
        var focus = new Trigger { Property = UIElement.IsKeyboardFocusedProperty, Value = true };
        focus.Setters.Add(new Setter(Control.BackgroundProperty, new SolidColorBrush(Color.FromRgb(219, 234, 254))));
        style.Triggers.Add(focus);
        var button = new Button
        {
            Width = 48,
            Background = Brushes.Transparent,
            Style = style,
            ToolTip = label,
            Cursor = Cursors.Hand,
            Content = new Path { Data = Geometry.Parse(geometry), Width = 12, Height = 12, Stretch = Stretch.Uniform, Stroke = new SolidColorBrush(Color.FromRgb(102, 112, 133)), StrokeThickness = 1.4 },
        };
        System.Windows.Automation.AutomationProperties.SetName(button, label);
        return button;
    }
}
