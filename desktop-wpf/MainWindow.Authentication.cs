using System.IO;
using System.ComponentModel;
using System.Security.Cryptography;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;

namespace AiReportDesktop;

public partial class MainWindow
{
    private bool _authenticationBusy;
    private bool _workspaceAccessAllowed;
    private int _backendSessionRevision;
    private TextBlock? _loginStatusText;
    private CoreWebView2? _authenticatedPreviewCore;
    private CancellationTokenSource? _loginCancellation;
    private TaskCompletionSource<bool>? _loginFinished;
    private bool _authenticationLifecycleReady;
    private bool _windowClosing;
    private bool _windowCloseReady;

    private bool CanUseActiveWorkspace => ActiveBackendSession.IsAuthenticated && _workspaceAccessAllowed;

    private void InitializeAuthenticationView()
    {
        if (!_authenticationLifecycleReady)
        {
            _authenticationLifecycleReady = true;
            Closing += OnAuthenticationWindowClosing;
            Closed += (_, _) => ActiveBackendSession.Dispose();
        }
        if (_loginStatusText == null && EnterButton.Parent is Panel panel)
        {
            _loginStatusText = Text("", 12, null, FindBrush("MutedBrush"), new Thickness(0, 12, 0, 0), true);
            panel.Children.Insert(panel.Children.IndexOf(EnterButton) + 1, _loginStatusText);
        }
        // The account API has no organization field. Do not silently register
        // an organization as the user's display name.
        OrganizationTextBox.Visibility = Visibility.Collapsed;
        foreach (var label in RegisterFields.Children.OfType<TextBlock>().Where(x => x.Text == "单位名称"))
            label.Visibility = Visibility.Collapsed;
        SetLoginStatus($"登录地址：{BackendBaseUrl}");
    }

    private void SetLoginStatus(string message, bool error = false)
    {
        if (_loginStatusText == null) return;
        _loginStatusText.Text = message;
        _loginStatusText.Foreground = error ? Brush(180, 35, 24) : FindBrush("MutedBrush");
    }

    private async Task AuthenticateWorkspaceAsync()
    {
        if (_authenticationBusy) return;
        InitializeAuthenticationView();
        var username = AccountTextBox.Text.Trim();
        var password = PasswordBox.Password;
        var registering = RegisterModeButton.IsChecked == true;
        if (string.IsNullOrWhiteSpace(username) || string.IsNullOrEmpty(password))
        {
            SetLoginStatus("请填写账号和密码。", true);
            return;
        }
        if (registering && password != ConfirmPasswordBox.Password)
        {
            SetLoginStatus("两次输入的密码不一致。", true);
            return;
        }
        _authenticationBusy = true;
        _loginFinished = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        EnterButton.IsEnabled = false;
        var registered = false;
        try
        {
            SetLoginStatus(registering ? "正在注册账号…" : "正在验证账号…");
            var session = ActiveBackendSession;
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            _loginCancellation = deadline;
            if (registering)
            {
                await session.RegisterAsync(username, password, ct: deadline.Token);
                registered = true;
            }
            await session.LoginAsync(username, password, deadline.Token);
            if (_windowClosing) return;
            await ActivateBackendSessionAsync(session);
        }
        catch (OperationCanceledException)
        {
            SetLoginStatus("连接超时，请确认后端服务已启动、地址可访问后重试。", true);
        }
        catch (Exception exception)
        {
            SetLoginStatus((registered ? "账号已注册，登录未完成：" : "登录未完成：") + exception.Message, true);
        }
        finally
        {
            PasswordBox.Clear();
            ConfirmPasswordBox.Clear();
            EnterButton.IsEnabled = true;
            _authenticationBusy = false;
            _loginCancellation = null;
            _loginFinished.TrySetResult(true);
        }
    }

    internal async Task ActivateBackendSessionAsync(BackendSession session)
    {
        if (_windowClosing) throw new InvalidOperationException("窗口正在关闭。");
        if (!session.IsAuthenticated)
            throw new InvalidOperationException("请先登录目标后端，再切换审查记录。");
        if (_isChecking || _completenessRunning)
            throw new InvalidOperationException("当前检查尚未结束，请等待完成后切换后端。");

        ActiveBackendSession = session;
        var revision = ++_backendSessionRevision;
        _workspaceAccessAllowed = true;
        ResetAuthenticatedWorkspace();
        LoadFunctionFindingState();
        UserTextBlock.Text = $"当前用户：{session.Username}（{(session.Role == "admin" ? "管理员" : "用户")}）";
        LoginPage.Visibility = Visibility.Collapsed;
        WorkbenchPage.Visibility = Visibility.Visible;
        StatusTextBlock.Text = $"已登录：{session.ApiBase}。";

        // Let the actual backend decide workspace access. This also works for a
        // server reached through a loopback SSH tunnel or a local backend on LAN.
        await LoadProjectsAsync();
        if (!IsCurrentBackendSession(session, revision)) return;
        ApplyWorkspaceNavigationAccess();
        if (!CanUseActiveWorkspace)
        {
            ShowPage("Records");
            ShowHistoryAccessNotice();
            return;
        }
        await LoadRecordsAsync();
        if (!IsCurrentBackendSession(session, revision)) return;
        await LoadRulesAsync();
        if (!IsCurrentBackendSession(session, revision)) return;
        ShowPage("Records");
    }

    private async Task LogoutWorkspaceAsync()
    {
        if (_authenticationBusy) return;
        if (_isChecking || _completenessRunning)
        {
            MessageBox.Show(this, "当前检查尚未结束，请等待完成后退出登录。", "检查进行中");
            return;
        }
        _authenticationBusy = true;
        var session = ActiveBackendSession;
        string? failure = null;
        // Hide and clear the old workspace before awaiting remote revocation.
        _workspaceAccessAllowed = false;
        ++_backendSessionRevision;
        WorkbenchPage.Visibility = Visibility.Collapsed;
        LoginPage.Visibility = Visibility.Visible;
        EnterButton.IsEnabled = false;
        ResetAuthenticatedWorkspace();
        try
        {
            try { await LogoutHistoryConnectionsAsync(); }
            catch (Exception exception) { failure = exception.Message; }
            try
            {
                using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(15));
                await session.LogoutAsync(deadline.Token);
            }
            catch (Exception exception) { failure ??= exception.Message; }
        }
        finally
        {
            session.ClearAuthentication();
            ActiveBackendSession = new BackendSession(session.ApiBase);
            PasswordBox.Clear();
            ConfirmPasswordBox.Clear();
            UserTextBlock.Text = "未登录";
            EnterButton.IsEnabled = true;
            _authenticationBusy = false;
            SetLoginStatus(failure == null ? $"已退出。登录地址：{BackendBaseUrl}"
                : "本地登录状态已清除；服务器会话撤销未确认：" + failure, failure != null);
        }
    }

    private void ResetAuthenticatedWorkspace()
    {
        _selectedProjectId = null;
        _selectedProjectName = null;
        _selectedCheckRunId = null;
        _activeRecordId = null;
        _projectTreeRoot = null;
        _runResults.Clear();
        _functionFindingSelections.Clear();
        _functionFindingCohorts.Clear();
        _rulesByModule.Clear();
        _ruleCheckBoxesByModule.Clear();
        RulesListPanel.Children.Clear();
        _completenessHistoryGeneration++;
        ProjectComboBox.ItemsSource = null;
        _projectTreePanel?.Children.Clear();
        _completenessHistory?.Children.Clear();
        ResultsListPanel.Children.Clear();
        ResultsSummaryPanel.Children.Clear();
        _completenessAttachments.Clear();
        _duplicateHistoryFiles.Clear();
        _duplicateHistoryChoiceCaptured = false;
        ReportPathTextBlock.Text = "尚未选择可研报告";
        UpdateCompletenessAttachmentLabel();
        ResetCompletenessResult();
        ResetRecordDetail();
        _documentWebView?.CoreWebView2?.Stop();
        _evidencePreviewLoadTask = null;
        _evidencePreviewLoadingUrl = null;
        ResetDocumentPreview("选择审查结果后显示当前账号的报告原文。");
        UpdateDeleteSelectionButtonState();
    }

    private void ApplyWorkspaceNavigationAccess()
    {
        foreach (var button in new[] { _reportNavButton, _specialistNavButton, _rulesNavButton, _settingsNavButton })
            if (button != null) button.IsEnabled = CanUseActiveWorkspace;
    }

    private void ShowHistoryAccessNotice()
    {
        var message = ActiveBackendSession.IsAuthenticated
            ? "已登录服务器。请打开“服务器收件箱”，将分配给你的审查记录保存到本地后查看。"
            : "请先登录后查看审查记录。";
        _projectTreePanel?.Children.Clear();
        _projectTreePanel?.Children.Add(Text(message, 13, null, FindBrush("MutedBrush"), new Thickness(0, 8, 0, 0), true));
        StatusTextBlock.Text = message;
    }

    private bool IsCurrentBackendSession(BackendSession session, int revision) =>
        ReferenceEquals(session, ActiveBackendSession) && revision == _backendSessionRevision && session.IsAuthenticated;

    private async void OnAuthenticationWindowClosing(object? sender, CancelEventArgs args)
    {
        if (_windowCloseReady) return;
        args.Cancel = true;
        if (_windowClosing) return;
        _windowClosing = true;
        IsEnabled = false;
        _workspaceAccessAllowed = false;
        ++_backendSessionRevision;
        _documentWebView?.CoreWebView2?.Stop();
        _loginCancellation?.Cancel();
        try
        {
            // Allow a canceled login to settle before revoking its final token.
            if (_loginFinished is { Task.IsCompleted: false })
            {
                try { await _loginFinished.Task.WaitAsync(TimeSpan.FromSeconds(2)); }
                catch (TimeoutException) { }
            }
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            await Task.WhenAll(ActiveBackendSession.LogoutAsync(deadline.Token), LogoutHistoryConnectionsAsync())
                .WaitAsync(TimeSpan.FromSeconds(6));
        }
        catch
        {
            // Window closure remains bounded even when either backend is down.
            // Revoking login sessions does not cancel already-running review jobs.
        }
        finally
        {
            ActiveBackendSession.ClearAuthentication();
            _windowCloseReady = true;
            Close();
        }
    }

    private static string GetAuthenticatedDisplayStatePath()
    {
        var scope = BackendBaseUrl + "\n" + (ActiveBackendSession.UserId ?? "signed-out");
        var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(scope))).ToLowerInvariant();
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "AIassist", "display-state", key + ".json");
    }

    private static bool IsActiveBackendApiUri(Uri uri)
    {
        var api = ActiveBackendSession.ApiBase;
        var prefix = api.AbsolutePath.TrimEnd('/');
        return uri.IsAbsoluteUri && string.IsNullOrEmpty(uri.UserInfo)
            && string.Equals(uri.Scheme, api.Scheme, StringComparison.OrdinalIgnoreCase)
            && string.Equals(uri.IdnHost, api.IdnHost, StringComparison.OrdinalIgnoreCase)
            && uri.Port == api.Port
            && (uri.AbsolutePath == prefix || uri.AbsolutePath.StartsWith(prefix + "/", StringComparison.Ordinal));
    }

    private static Uri AuthenticatedBackendUri(string address)
    {
        if (!ActiveBackendSession.IsAuthenticated)
            throw new InvalidOperationException("请先登录后端。");
        if (!Uri.TryCreate(address, UriKind.Absolute, out var uri) || !IsActiveBackendApiUri(uri))
            throw new InvalidOperationException("该地址不属于当前登录后端，已取消请求。");
        return uri;
    }

    private async Task EnsureAuthenticatedPreviewAsync()
    {
        if (_documentWebView == null) return;
        await _documentWebView.EnsureCoreWebView2Async();
        var core = _documentWebView.CoreWebView2;
        if (ReferenceEquals(_authenticatedPreviewCore, core)) return;
        _authenticatedPreviewCore = core;
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
        core.WebResourceRequested += (_, args) =>
        {
            // Remove any previously attached header first, including redirects.
            // The token is read at request time and never captured by a page.
            if (args.Request.Headers.Contains("Authorization"))
                args.Request.Headers.RemoveHeader("Authorization");
            if (ActiveBackendSession.IsAuthenticated
                && Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out var uri) && IsActiveBackendApiUri(uri)
                && ActiveBackendSession.Client.DefaultRequestHeaders.Authorization is { Scheme: "Bearer" } auth)
                args.Request.Headers.SetHeader("Authorization", auth.ToString());
        };
        core.NavigationStarting += (_, args) =>
        {
            if (args.Uri == "about:blank") return;
            if (!ActiveBackendSession.IsAuthenticated
                || !Uri.TryCreate(args.Uri, UriKind.Absolute, out var uri) || !IsActiveBackendApiUri(uri))
            {
                args.Cancel = true;
                if (_documentLocationTextBlock != null)
                    _documentLocationTextBlock.Text = "已阻止打开当前后端之外的预览地址。";
            }
        };
        core.NewWindowRequested += (_, args) => args.Handled = true;
    }
}
