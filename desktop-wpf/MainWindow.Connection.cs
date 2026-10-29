using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;

namespace AiReportDesktop;

public partial class MainWindow
{
    private TextBox? _backendLocalUrlBox;
    private TextBox? _backendServerUrlBox;
    private TextBlock? _backendConnectionStatus;
    private bool _updatingBackendSelection = true;
    private readonly Dictionary<string, string> _backendSessions = new(StringComparer.Ordinal);
    private string _desktopUserName = "本地工作台";

    private UIElement BuildConnectionSettingsPanel()
    {
        _backendLocalUrlBox = Input(BackendConnection.LocalUrl);
        _backendServerUrlBox = Input(BackendConnection.ServerUrl);
        _backendConnectionStatus = Text("在左侧选择检测后端；修改地址后点击应用。", 12, null, FindBrush("MutedBrush"), null, true);
        var apply = Button("应用地址并连接", false);
        apply.Click += async (_, _) => await SwitchBackendAsync(WorkbenchBackendMode.SelectedIndex == 1);
        return SettingsPanel("服务连接", "在工作台内切换本地或服务器后端，无需退出登录。", new UIElement[]
        {
            SettingRow("本地 API 地址", "本机审查服务", _backendLocalUrlBox),
            SettingRow("服务器 API 地址", "服务器审查服务", _backendServerUrlBox),
            ActionRow(apply),
            _backendConnectionStatus
        }, new Thickness(0, 14, 0, 0));
    }
    private bool _authBusy;
    private bool _previewAuthInstalled;
    private bool ConnectionBusy => _authBusy || _isChecking || _completenessRunning || BackendConnection.HasActiveRequests;

    private void InitializeConnection()
    {
        RefreshBackendSelection();
        LoginBackendStatus.Text = "";
        BackendConnection.Unauthorized += HandleSessionExpired;
        Closed += (_, _) => BackendConnection.Unauthorized -= HandleSessionExpired;
    }

    private void HandleSessionExpired() => Dispatcher.BeginInvoke(new Action(() =>
    {
        _backendSessions.Remove(BackendBaseUrl);
        SetBackendStatus("当前后端会话已过期，请重新连接并登录；可在左侧切换后端。");
    }));

    private void ResetConnectionWorkspace()
    {
        _selectedProjectId = _selectedProjectName = _selectedCheckRunId = _activeRecordId = _activePreviewUrl = null;
        _projectTreeRoot = null;
        _preciseEvidenceGeneration++;
        _evidencePreviewLoadTask = null;
        _evidencePreviewLoadingUrl = null;
        ProjectComboBox.ItemsSource = null;
        ProjectComboBox.Items.Clear();
        _rulesByModule.Clear();
        _ruleCheckBoxesByModule.Clear();
        _runResults.Clear();
        _previewUrlsByRole.Clear();
        _previewNamesByRole.Clear();
        _projectTreePanel?.Children.Clear();
        _recordDetailPanel?.Children.Clear();
        _rulesDirectoryPanel?.Children.Clear();
        _rulesEditorPanel?.Children.Clear();
        RulesListPanel.Children.Clear();
        ResultsListPanel.Children.Clear();
        ResultsSummaryPanel.Children.Clear();
        _completenessHistory?.Children.Clear();
        ResetCompletenessResult();
        _documentWebView?.CoreWebView2?.Stop();
        _documentWebView?.CoreWebView2?.NavigateToString("<html><body>请登录后选择审查记录。</body></html>");
    }

    private void RefreshBackendSelection()
    {
        _updatingBackendSelection = true;
        WorkbenchBackendMode.SelectedIndex = BackendConnection.UseServer ? 1 : 0;
        WorkbenchBackendStatus.Text = $"当前：{(BackendConnection.UseServer ? "服务器" : "本地")}\n{BackendBaseUrl}";
        _updatingBackendSelection = false;
    }

    private void SetBackendStatus(string message)
    {
        WorkbenchBackendStatus.Text = message;
        if (_backendConnectionStatus != null) _backendConnectionStatus.Text = message;
    }

    private async void OnWorkbenchBackendChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_updatingBackendSelection || WorkbenchPage?.Visibility != Visibility.Visible) return;
        await SwitchBackendAsync(WorkbenchBackendMode.SelectedIndex == 1);
    }

    private async void OnReconnectBackendClicked(object sender, RoutedEventArgs e) =>
        await SwitchBackendAsync(BackendConnection.UseServer);

    private async Task SwitchBackendAsync(bool useServer)
    {
        if (ConnectionBusy)
        {
            RefreshBackendSelection();
            SetBackendStatus("任务或请求正在执行，请完成后再切换。");
            return;
        }
        _authBusy = true;
        WorkbenchBackendMode.IsEnabled = false;
        var switched = false;
        try
        {
            var local = BackendConnection.Normalize(_backendLocalUrlBox!.Text);
            var server = BackendConnection.Normalize(_backendServerUrlBox!.Text);
            var target = useServer ? server : local;
            if (BackendConnection.Token is { } currentToken) _backendSessions[BackendBaseUrl] = currentToken;
            _backendSessions.TryGetValue(target, out var token);
            SetBackendStatus("正在连接目标后端…");
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(12));
            if (!await BackendConnection.ValidateEndpointAsync(target, !useServer, token, deadline.Token))
            {
                _backendSessions.Remove(target);
                token = await ShowBackendLoginAsync(target);
                if (token == null) { SetBackendStatus("已取消，继续使用原后端。"); return; }
            }
            BackendConnection.Configure(useServer, local, server);
            BackendConnection.Token = token;
            switched = true;
            if (token != null) _backendSessions[target] = token;
            ResetConnectionWorkspace();
            await LoadProjectsAsync();
            await LoadRulesAsync();
            await LoadRecordsAsync();
            SetBackendStatus($"已连接{(useServer ? "服务器" : "本地")}后端：{BackendBaseUrl}");
        }
        catch (Exception ex) { SetBackendStatus(switched ? $"已切换后端，刷新数据失败：{ex.Message}" : $"切换失败，当前连接保留：{ex.Message}"); }
        finally
        {
            _authBusy = false;
            WorkbenchBackendMode.IsEnabled = true;
            var status = WorkbenchBackendStatus.Text;
            RefreshBackendSelection();
            SetBackendStatus(status);
        }
    }

    private Task<string?> ShowBackendLoginAsync(string target)
    {
        var dialog = new Window { Owner = this, Title = "连接后端账号", Width = 460, SizeToContent = SizeToContent.Height,
            WindowStartupLocation = WindowStartupLocation.CenterOwner, ResizeMode = ResizeMode.NoResize };
        var panel = new StackPanel { Margin = new Thickness(24) };
        panel.Children.Add(Text($"请登录目标后端\n{target}", 14, null, null, new Thickness(0, 0, 0, 14), true));
        panel.Children.Add(Text("账号", 12));
        var account = Input(_desktopUserName == "本地工作台" ? "" : _desktopUserName);
        panel.Children.Add(account);
        panel.Children.Add(Text("密码", 12));
        var password = new PasswordBox { Padding = new Thickness(10), Margin = new Thickness(0, 4, 0, 12) };
        panel.Children.Add(password);
        var register = new CheckBox { Content = "注册新账号（密码至少12位）", Margin = new Thickness(0, 0, 0, 10) };
        panel.Children.Add(register);
        var error = Text("", 12, null, null, null, true);
        panel.Children.Add(error);
        var login = Button("连接", true);
        var cancel = Button("取消", false);
        cancel.Click += (_, _) => dialog.DialogResult = false;
        panel.Children.Add(ActionRow(login, cancel));
        string? token = null;
        var connecting = false;
        dialog.Closing += (_, e) => { if (connecting) e.Cancel = true; };
        login.Click += async (_, _) =>
        {
            connecting = true;
            login.IsEnabled = false;
            cancel.IsEnabled = false;
            try
            {
                token = await AuthenticateEndpointAsync(target, account.Text.Trim(), password.Password, register.IsChecked == true,
                    () => register.IsChecked = false);
                password.Clear();
                connecting = false;
                dialog.DialogResult = true;
            }
            catch (Exception ex) { error.Text = ex.Message; }
            finally { connecting = false; login.IsEnabled = true; cancel.IsEnabled = true; }
        };
        dialog.Content = panel;
        dialog.ShowDialog();
        password.Clear();
        return Task.FromResult(token);
    }

    private static async Task<string> AuthenticateEndpointAsync(string target, string username, string password, bool register,
        Action? onRegistered = null)
    {
        if (string.IsNullOrWhiteSpace(username) || string.IsNullOrEmpty(password))
            throw new InvalidOperationException("请输入账号和密码。");
        using var client = BackendConnection.CreateEndpointClient(target);
        if (register)
        {
            using var registration = await client.PostAsync("auth/register", new StringContent(
                JsonSerializer.Serialize(new { username, password }), Encoding.UTF8, "application/json"));
            if (!registration.IsSuccessStatusCode) throw new InvalidOperationException(ExtractErrorMessage(await registration.Content.ReadAsStringAsync()));
            onRegistered?.Invoke();
        }
        using var response = await client.PostAsync("auth/login", new StringContent(
            JsonSerializer.Serialize(new { username, password }), Encoding.UTF8, "application/json"));
        var body = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException(ExtractErrorMessage(body));
        using var json = JsonDocument.Parse(body);
        return json.RootElement.GetProperty("access_token").GetString() ?? throw new InvalidOperationException("后端未返回登录令牌。");
    }

    private async Task EnterBackendAsync()
    {
        if (ConnectionBusy) return;
        _authBusy = true;
        EnterButton.IsEnabled = false;
        try
        {
            var username = AccountTextBox.Text.Trim();
            var password = PasswordBox.Password;
            var register = RegisterModeButton.IsChecked == true;
            if (register && password != ConfirmPasswordBox.Password) throw new InvalidOperationException("两次密码不一致。");
            // Desktop identity is authenticated by the account service; the business endpoint is chosen after login.
            var serverToken = await AuthenticateEndpointAsync(BackendConnection.ServerUrl, username, password, register,
                () => LoginModeButton.IsChecked = true);
            _backendSessions[BackendConnection.ServerUrl] = serverToken;
            _backendSessions.TryGetValue(BackendBaseUrl, out var activeToken);
            BackendConnection.Token = activeToken;
            UserTextBlock.Text = $"当前用户：{username}";
            _desktopUserName = username;
            RefreshBackendSelection();
            PasswordBox.Clear();
            ConfirmPasswordBox.Clear();
            LoginPage.Visibility = Visibility.Collapsed;
            WorkbenchPage.Visibility = Visibility.Visible;
            // A saved local endpoint may be stopped. The user can still enter and choose the server.
            try
            {
                using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(8));
                if (await BackendConnection.ValidateEndpointAsync(BackendBaseUrl, !BackendConnection.UseServer, activeToken, deadline.Token))
                {
                    await LoadProjectsAsync();
                    await LoadRulesAsync();
                }
                else SetBackendStatus("当前检测后端需要独立登录，请点击重新连接或切换后端。");
            }
            catch (Exception ex) { SetBackendStatus($"当前检测后端不可用，可在左侧切换：{ex.Message}"); }
        }
        catch (Exception ex) { LoginBackendStatus.Text = $"登录失败：{ex.Message}"; }
        finally { _authBusy = false; EnterButton.IsEnabled = true; }
    }

    private async Task LogoutBackendAsync()
    {
        if (ConnectionBusy) { MessageBox.Show(this, "任务或请求正在执行，请完成后再退出或切换后端。"); return; }
        _authBusy = true;
        string? logoutError = null;
        try
        {
            if (BackendConnection.Token is { } currentToken) _backendSessions[BackendBaseUrl] = currentToken;
            foreach (var session in _backendSessions)
            {
                try
                {
                    using var client = BackendConnection.CreateEndpointClient(session.Key, session.Value);
                    using var response = await client.PostAsync("auth/logout", null);
                    if (response.StatusCode != HttpStatusCode.Unauthorized) response.EnsureSuccessStatusCode();
                }
                catch (Exception ex) { logoutError = ex.Message; }
            }
        }
        catch (Exception ex) { logoutError = ex.Message; }
        finally
        {
            BackendConnection.Token = null;
            _backendSessions.Clear();
            _desktopUserName = "本地工作台";
            ResetConnectionWorkspace();
            WorkbenchPage.Visibility = Visibility.Collapsed;
            LoginPage.Visibility = Visibility.Visible;
            LoginBackendStatus.Text = logoutError == null ? $"已退出；当前后端：{BackendBaseUrl}" : $"本机会话已清除；服务器退出失败：{logoutError}";
            _authBusy = false;
        }
    }

    private void NavigateAuthenticatedPreview(Uri uri)
    {
        if (!BackendConnection.IsApiUrl(uri)) throw new InvalidOperationException("原文地址不属于当前后端。");
        var core = _documentWebView!.CoreWebView2;
        if (!_previewAuthInstalled)
        {
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += (_, args) =>
            {
                if (Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out var target) && BackendConnection.IsApiUrl(target) && BackendConnection.Token is { } token)
                    args.Request.Headers.SetHeader("Authorization", "Bearer " + token);
            };
            core.NavigationStarting += (_, args) =>
            {
                if (args.Uri.StartsWith("http", StringComparison.OrdinalIgnoreCase) &&
                    (!Uri.TryCreate(args.Uri, UriKind.Absolute, out var target) || !BackendConnection.IsApiUrl(target))) args.Cancel = true;
            };
            core.WebResourceResponseReceived += (_, args) =>
            {
                if (args.Response.StatusCode == 401 && Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out var target) && BackendConnection.IsApiUrl(target))
                {
                    BackendConnection.Token = null;
                    HandleSessionExpired();
                }
            };
            _previewAuthInstalled = true;
        }
        var headers = BackendConnection.Token is { } accessToken ? "Authorization: Bearer " + accessToken + "\r\n" : "";
        var request = core.Environment.CreateWebResourceRequest(uri.AbsoluteUri, "GET", null, headers);
        core.NavigateWithWebResourceRequest(request);
    }
}
