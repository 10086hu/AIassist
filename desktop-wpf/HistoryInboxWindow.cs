using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;

namespace AiReportDesktop;

/// <summary>Receive addressed server records into a separately authenticated local backend.</summary>
public sealed class HistoryInboxWindow : Window
{
    private const int PageSize = 50;
    private readonly ObservableCollection<InboxRow> _items = new();
    private readonly DataGrid _grid = new();
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _page = new();
    private readonly ComboBox _filter = new();
    private readonly Button _refresh = ActionButton("刷新收件箱");
    private readonly Button _previous = ActionButton("上一页");
    private readonly Button _next = ActionButton("下一页");
    private readonly Button _receive = ActionButton("保存到本地");
    private readonly Button _view = ActionButton("查看本地记录");
    private readonly Button _cancel = ActionButton("取消传输");
    private readonly StackPanel _serverPanel = new();
    private readonly StackPanel _localPanel = new();
    private CancellationTokenSource? _operation;
    private int _offset;
    private bool _busy;

    public BackendSession? ServerSession { get; private set; }
    public BackendSession? LocalSession { get; private set; }
    public string? LocalProjectId { get; private set; }
    public string? LastReceiveMessage { get; private set; }
    public bool ViewLocalRecords { get; private set; }

    public HistoryInboxWindow(BackendSession? server, BackendSession? local)
    {
        ServerSession = server;
        LocalSession = local;
        Title = "服务器收件箱";
        Width = 1000;
        Height = 760;
        MinWidth = 880;
        MinHeight = 650;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = new SolidColorBrush(Color.FromRgb(248, 250, 252));
        FontFamily = new FontFamily("Microsoft YaHei UI");
        FontSize = 13;
        var root = new Grid { Margin = new Thickness(24) };
        foreach (var height in new[] { GridLength.Auto, GridLength.Auto, GridLength.Auto,
                     new GridLength(1, GridUnitType.Star), GridLength.Auto, GridLength.Auto })
            root.RowDefinitions.Add(new RowDefinition { Height = height });
        var heading = new StackPanel();
        heading.Children.Add(new TextBlock { Text = "接收服务器审查记录", FontSize = 25, FontWeight = FontWeights.SemiBold });
        heading.Children.Add(new TextBlock { Text = "领取管理员发给你的记录和原始报告，保存到本机后即可通过本地后端查看。",
            Foreground = Brushes.SlateGray, Margin = new Thickness(0, 7, 0, 16), TextWrapping = TextWrapping.Wrap });
        root.Children.Add(heading);
        var connections = new Grid { Margin = new Thickness(0, 0, 0, 14) };
        connections.ColumnDefinitions.Add(new ColumnDefinition());
        connections.ColumnDefinitions.Add(new ColumnDefinition());
        var serverCard = ConnectionCard(_serverPanel);
        serverCard.Margin = new Thickness(0, 0, 8, 0);
        connections.Children.Add(serverCard);
        var localCard = ConnectionCard(_localPanel);
        localCard.Margin = new Thickness(8, 0, 0, 0);
        Grid.SetColumn(localCard, 1);
        connections.Children.Add(localCard);
        Grid.SetRow(connections, 1);
        root.Children.Add(connections);
        BuildConnectionPanel(_serverPanel, false);
        BuildConnectionPanel(_localPanel, true);

        var toolbar = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 12) };
        _filter.Items.Add("待接收");
        _filter.Items.Add("已接收");
        _filter.Items.Add("全部");
        _filter.SelectedIndex = 0;
        _filter.MinWidth = 100;
        _filter.Margin = new Thickness(0, 0, 10, 0);
        toolbar.Children.Add(_filter);
        toolbar.Children.Add(_refresh);
        toolbar.Children.Add(new TextBlock { Text = "已接收记录可再次保存到当前电脑。", Foreground = Brushes.SlateGray,
            VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(12, 0, 0, 0) });
        Grid.SetRow(toolbar, 2);
        root.Children.Add(toolbar);
        _grid.AutoGenerateColumns = false;
        _grid.IsReadOnly = true;
        _grid.SelectionMode = DataGridSelectionMode.Single;
        _grid.CanUserAddRows = false;
        _grid.HeadersVisibility = DataGridHeadersVisibility.Column;
        _grid.RowHeight = 38;
        _grid.ItemsSource = _items;
        _grid.Columns.Add(Column("记录编号", nameof(InboxRow.Label), 2));
        _grid.Columns.Add(Column("分发时间", nameof(InboxRow.Time), 2));
        _grid.Columns.Add(Column("服务器状态", nameof(InboxRow.ServerStatus), 1));
        _grid.Columns.Add(Column("本次接收", nameof(InboxRow.LocalStatus), 2));
        _grid.SelectionChanged += (_, _) => UpdateButtons();
        Grid.SetRow(_grid, 3);
        root.Children.Add(_grid);

        var bottom = new DockPanel { Margin = new Thickness(0, 12, 0, 12) };
        var paging = new StackPanel { Orientation = Orientation.Horizontal };
        paging.Children.Add(_previous);
        paging.Children.Add(_page);
        _page.VerticalAlignment = VerticalAlignment.Center;
        _page.Margin = new Thickness(10, 0, 10, 0);
        paging.Children.Add(_next);
        bottom.Children.Add(paging);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        actions.Children.Add(_cancel);
        actions.Children.Add(_receive);
        actions.Children.Add(_view);
        bottom.Children.Add(actions);
        Grid.SetRow(bottom, 4);
        root.Children.Add(bottom);
        _status.Text = "先连接服务器。接收前还需要登录本地后端；首次使用本地账号可注册。";
        _status.Foreground = Brushes.SlateGray;
        Grid.SetRow(_status, 5);
        root.Children.Add(_status);
        Content = root;

        _refresh.Click += async (_, _) => { _offset = 0; await LoadInboxAsync(); };
        _previous.Click += async (_, _) => { _offset = Math.Max(0, _offset - PageSize); await LoadInboxAsync(); };
        _next.Click += async (_, _) => { _offset += PageSize; await LoadInboxAsync(); };
        _filter.SelectionChanged += async (_, _) => { _offset = 0; if (IsLoaded) await LoadInboxAsync(); };
        _receive.Click += async (_, _) => await ReceiveSelectedAsync();
        _cancel.Click += (_, _) => _operation?.Cancel();
        _view.Click += (_, _) => { ViewLocalRecords = true; DialogResult = true; };
        Closing += (_, e) =>
        {
            if (!_busy) return;
            e.Cancel = true;
            _operation?.Cancel();
            _status.Text = "正在停止传输，请完成后关闭窗口。已保存的记录会保留。";
        };
        Loaded += async (_, _) => { if (ServerSession?.IsAuthenticated == true) await LoadInboxAsync(); };
        UpdateButtons();
    }

    private static Button ActionButton(string label) => new()
    {
        Content = label, Padding = new Thickness(13, 8, 13, 8), Margin = new Thickness(0, 0, 7, 0),
        MinHeight = 34
    };

    private static Border ConnectionCard(UIElement content) => new()
    {
        Child = content, Background = Brushes.White, Padding = new Thickness(15),
        BorderBrush = Brushes.LightGray, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(7)
    };

    private static DataGridTextColumn Column(string title, string field, double width) => new()
    {
        Header = title, Binding = new Binding(field), Width = new DataGridLength(width, DataGridLengthUnitType.Star)
    };

    private void BuildConnectionPanel(StackPanel panel, bool local)
    {
        panel.Children.Clear();
        var session = local ? LocalSession : ServerSession;
        panel.Children.Add(new TextBlock { Text = local ? "保存到本机" : "服务器账号", FontSize = 16, FontWeight = FontWeights.SemiBold });
        if (session?.IsAuthenticated == true)
        {
            panel.Children.Add(new TextBlock { Text = "已登录：" + session.Username, Margin = new Thickness(0, 12, 0, 6) });
            panel.Children.Add(new TextBlock { Text = session.ApiBase.ToString(), Foreground = Brushes.SlateGray, TextWrapping = TextWrapping.Wrap });
            panel.Children.Add(new TextBlock { Text = "退出主界面登录后可更换账号。", Foreground = Brushes.SlateGray, Margin = new Thickness(0, 10, 0, 0) });
            return;
        }
        var address = new TextBox
        {
            Text = session?.ApiBase.ToString() ?? (Environment.GetEnvironmentVariable(local ? "AIASSIST_LOCAL_BACKEND_URL" : "AIASSIST_SERVER_URL")
                ?? (local ? "http://127.0.0.1:8000/api" : "http://10.60.150.179:8000/api")),
            Margin = new Thickness(0, 10, 0, 8), Padding = new Thickness(6), ToolTip = local ? "本地后端地址" : "服务器地址"
        };
        panel.Children.Add(address);
        var fields = new Grid();
        fields.ColumnDefinitions.Add(new ColumnDefinition());
        fields.ColumnDefinitions.Add(new ColumnDefinition());
        var username = new TextBox { Padding = new Thickness(6), Margin = new Thickness(0, 0, 8, 0), ToolTip = "账号" };
        var password = new PasswordBox { Padding = new Thickness(6), ToolTip = "密码" };
        fields.Children.Add(username);
        Grid.SetColumn(password, 1);
        fields.Children.Add(password);
        panel.Children.Add(new TextBlock { Text = "账号                                  密码", Foreground = Brushes.SlateGray, Margin = new Thickness(0, 0, 0, 3) });
        panel.Children.Add(fields);
        var login = ActionButton(local ? "登录本地" : "登录服务器");
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 10, 0, 0) };
        buttons.Children.Add(login);
        panel.Children.Add(buttons);
        PasswordBox? confirmation = null;
        if (local)
        {
            confirmation = new PasswordBox { Padding = new Thickness(6), Margin = new Thickness(0, 8, 0, 0), ToolTip = "注册时再次输入密码" };
            var confirmRow = new StackPanel { Visibility = Visibility.Collapsed };
            confirmRow.Children.Add(new TextBlock { Text = "注册时再次输入密码", Foreground = Brushes.SlateGray });
            confirmRow.Children.Add(confirmation);
            panel.Children.Insert(panel.Children.Count - 1, confirmRow);
            var register = ActionButton("注册本地账号");
            buttons.Children.Add(register);
            register.Click += async (_, _) =>
            {
                if (confirmRow.Visibility != Visibility.Visible)
                {
                    confirmRow.Visibility = Visibility.Visible;
                    _status.Text = "本地注册密码至少12位；确认密码后再次点击“注册本地账号”。";
                    return;
                }
                if (password.Password != confirmation.Password)
                {
                    _status.Text = "两次输入的密码不一致。";
                    return;
                }
                await ConnectAsync(local, address.Text, username.Text, password, confirmation, true);
            };
        }
        login.Click += async (_, _) => await ConnectAsync(local, address.Text, username.Text, password, confirmation, false);
    }

    private async Task ConnectAsync(bool local, string address, string username, PasswordBox password,
                                    PasswordBox? confirmation, bool register)
    {
        var secret = password.Password;
        var connected = false;
        await RunAsync(async token =>
        {
            var uri = new Uri(address.Trim(), UriKind.Absolute);
            if (local && !uri.IsLoopback) throw new InvalidOperationException("本地接收地址必须是 localhost 或本机回环地址。");
            var candidate = new BackendSession(uri);
            try
            {
                if (register) await candidate.RegisterAsync(username.Trim(), secret, username.Trim(), token);
                await candidate.LoginAsync(username.Trim(), secret, token);
                if (local) LocalSession = candidate; else ServerSession = candidate;
                connected = true;
            }
            catch
            {
                candidate.Dispose();
                throw;
            }
            password.Clear();
            confirmation?.Clear();
            BuildConnectionPanel(local ? _localPanel : _serverPanel, local);
            _status.Text = local ? "本地已连接，可以选择记录并保存。" : "服务器已连接，正在读取收件箱。";
        });
        if (connected && !local) await LoadInboxAsync();
    }

    private HistoryTransferClient Transfer() => new(ServerSession ?? throw new InvalidOperationException("请先登录服务器。"),
        LocalSession ?? throw new InvalidOperationException("请先登录本地后端。"));

    private async Task LoadInboxAsync()
    {
        if (ServerSession?.IsAuthenticated != true || _busy) return;
        await RunAsync(async token =>
        {
            _items.Clear();
            _status.Text = "正在读取服务器收件箱…";
            // Listing needs no local account; the service validates the destination at receive time.
            using var temporaryLocal = LocalSession == null ? new BackendSession(new Uri("http://127.0.0.1:8000/api")) : null;
            var transfer = new HistoryTransferClient(ServerSession, LocalSession ?? temporaryLocal!);
            var filter = _filter.SelectedIndex switch { 0 => "pending", 1 => "received", _ => null };
            var deliveries = await transfer.ListInboxAsync(_offset, PageSize, filter, token);
            foreach (var delivery in deliveries) _items.Add(new InboxRow(delivery));
            _page.Text = $"第 {_offset / PageSize + 1} 页";
            _status.Text = _items.Count == 0 ? "当前没有符合条件的收件。" : "选择一条记录，点击“保存到本地”。原始报告会一并保存。";
        });
    }

    private async Task ReceiveSelectedAsync()
    {
        if (_grid.SelectedItem is not InboxRow row || _busy) return;
        await RunAsync(async token =>
        {
            _status.Text = "正在下载并保存记录和原始报告，请稍候…";
            row.LocalStatus = "传输中";
            try
            {
                var received = await Transfer().ReceiveAsync(row.Delivery, token);
                LocalProjectId = received.LocalProjectId;
                LastReceiveMessage = received.Message;
                row.LocalStatus = received.Acknowledged ? (received.Duplicate ? "本地已有，已确认" : "已保存并确认") : "已保存，待确认";
                if (received.Acknowledged) row.ServerStatus = "已接收";
                _status.Text = received.Message + (received.Acknowledged ? " 可点击“查看本地记录”打开。" : " 再次点击保存可补发接收确认。");
            }
            catch
            {
                row.LocalStatus = "未完成，可重试";
                throw;
            }
        });
    }

    private async Task RunAsync(Func<CancellationToken, Task> action)
    {
        if (_busy) return;
        var serverWasAuthenticated = ServerSession?.IsAuthenticated == true;
        var localWasAuthenticated = LocalSession?.IsAuthenticated == true;
        _busy = true;
        _operation = new CancellationTokenSource();
        UpdateButtons();
        try { await action(_operation.Token); }
        catch (OperationCanceledException) { _status.Text = "操作已取消。可重新领取；已完成的本地导入不会重复写入。"; }
        catch (Exception error)
        {
            _status.Text = error is BackendRequestException ? error.Message : error is System.Net.Http.HttpRequestException
                ? "连接失败。请确认服务器可访问，且本地后端已通过 run_backend.bat 启动。"
                : error.Message;
        }
        finally
        {
            _operation.Dispose();
            _operation = null;
            _busy = false;
            if (serverWasAuthenticated && ServerSession?.IsAuthenticated != true)
                BuildConnectionPanel(_serverPanel, false);
            if (localWasAuthenticated && LocalSession?.IsAuthenticated != true)
                BuildConnectionPanel(_localPanel, true);
            UpdateButtons();
        }
    }

    private void UpdateButtons()
    {
        var server = ServerSession?.IsAuthenticated == true;
        _serverPanel.IsEnabled = !_busy;
        _localPanel.IsEnabled = !_busy;
        _filter.IsEnabled = !_busy;
        _refresh.IsEnabled = !_busy && server;
        _previous.IsEnabled = !_busy && server && _offset > 0;
        _next.IsEnabled = !_busy && server && _items.Count == PageSize;
        _receive.IsEnabled = !_busy && server && LocalSession?.IsAuthenticated == true
            && _grid.SelectedItem is InboxRow row && row.Delivery.Status != "revoked";
        _view.IsEnabled = !_busy && LocalProjectId != null;
        _cancel.IsEnabled = _busy;
    }

    private sealed class InboxRow : INotifyPropertyChanged
    {
        public HistoryDelivery Delivery { get; }
        public string Label => Delivery.Id.Length > 12 ? Delivery.Id[..12] : Delivery.Id;
        public string Time => DateTimeOffset.TryParse(Convert.ToString(Delivery.CreatedAt), out var time)
            ? time.ToLocalTime().ToString("yyyy-MM-dd HH:mm") : Convert.ToString(Delivery.CreatedAt) ?? "";
        private string _serverStatus;
        private string _localStatus = "尚未保存";
        public string ServerStatus { get => _serverStatus; set { _serverStatus = value; Changed(nameof(ServerStatus)); } }
        public string LocalStatus { get => _localStatus; set { _localStatus = value; Changed(nameof(LocalStatus)); } }
        public event PropertyChangedEventHandler? PropertyChanged;
        private void Changed(string name) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        public InboxRow(HistoryDelivery delivery)
        {
            Delivery = delivery;
            _serverStatus = delivery.Status switch { "pending" => "待接收", "received" => "已接收", "revoked" => "已撤回", _ => delivery.Status };
        }
    }
}
