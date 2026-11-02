using Microsoft.Win32;
using System.IO;
using System.Net;
using System.Text.Json;
using System.Windows;

namespace AiReportDesktop;

public partial class MainWindow
{
    private BackendSession? _historyServerSession;
    private BackendSession? _historyLocalSession;
    private bool _historyInboxOpen;

    private async void OnOpenHistoryInbox(object sender, RoutedEventArgs e)
    {
        if (_historyInboxOpen) return;
        if (_isChecking || _completenessRunning)
        {
            MessageBox.Show(this, "请等待当前审查结束后再接收服务器记录。", "审查正在运行", MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }
        _historyInboxOpen = true;
        try
        {
        var active = ActiveBackendSession;
        // A server may be reached through a loopback SSH tunnel. Probe the
        // server-only inbox instead of treating every loopback session as local.
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            using var probe = await active.Client.GetAsync(active.ResolveApiUri("history/inbox?limit=1"), timeout.Token);
            if (probe.IsSuccessStatusCode) _historyServerSession = active;
            else if (probe.StatusCode == HttpStatusCode.Forbidden && HistoryTransferClient.IsLocalBackend(active.ApiBase))
                _historyLocalSession = active;
            else if (probe.StatusCode == HttpStatusCode.Unauthorized)
            {
                MessageBox.Show(this, "当前登录已失效，请退出后重新登录。", "需要登录");
                return;
            }
        }
        catch (Exception error) when (error is System.Net.Http.HttpRequestException or OperationCanceledException)
        {
            // The inbox remains usable for connecting to the other backend.
            if (HistoryTransferClient.IsLocalBackend(active.ApiBase)) _historyLocalSession = active;
            else _historyServerSession = active;
        }
        var inbox = new HistoryInboxWindow(_historyServerSession, _historyLocalSession) { Owner = this };
        inbox.ShowDialog();
        _historyServerSession = inbox.ServerSession;
        _historyLocalSession = inbox.LocalSession;
        if (inbox.LastReceiveMessage != null) StatusTextBlock.Text = inbox.LastReceiveMessage;
        if (!inbox.ViewLocalRecords || inbox.LocalSession == null) return;
        try
        {
            await ActivateBackendSessionAsync(inbox.LocalSession);
            _selectedProjectId = inbox.LocalProjectId;
            ProjectComboBox.SelectedItem = ProjectComboBox.Items.OfType<ProjectOption>()
                .FirstOrDefault(project => project.Id == inbox.LocalProjectId);
            ShowPage("Records");
            await LoadRecordsAsync();
            StatusTextBlock.Text = "正在查看本地审查记录，原始报告已保存到本机数据库。";
        }
        catch (Exception error)
        {
            MessageBox.Show(this, error.Message, "本地记录打开失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        }
        finally { _historyInboxOpen = false; }
    }

    private void AddOriginalFileDownloads(JsonElement record)
    {
        if (_recordDetailPanel == null || !record.TryGetProperty("source_documents", out var sources)
            || sources.ValueKind != JsonValueKind.Array) return;
        var session = ActiveBackendSession;
        foreach (var source in sources.EnumerateArray())
        {
            var filename = GetString(source, "filename");
            var path = GetString(source, "download_url");
            if (string.IsNullOrWhiteSpace(path)) continue;
            var download = Button("另存原始报告：" + filename, false);
            download.Margin = new Thickness(0, 4, 0, 8);
            download.Click += async (_, _) =>
            {
                var save = new SaveFileDialog { FileName = Path.GetFileName(filename), Filter = "原始报告|*.*", OverwritePrompt = true };
                if (save.ShowDialog(this) != true) return;
                download.IsEnabled = false;
                try
                {
                    await session.DownloadFileAsync(path, save.FileName);
                    StatusTextBlock.Text = "原始报告已保存：" + save.FileName;
                }
                catch (Exception error)
                {
                    MessageBox.Show(this, "保存失败：" + error.Message, "原始报告", MessageBoxButton.OK, MessageBoxImage.Warning);
                }
                finally { download.IsEnabled = true; }
            };
            _recordDetailPanel.Children.Add(download);
        }
    }

    private async Task LogoutHistoryConnectionsAsync()
    {
        var sessions = new[] { _historyServerSession, _historyLocalSession }.Where(session => session != null).Distinct();
        await Task.WhenAll(sessions.Where(session => !ReferenceEquals(session, ActiveBackendSession)).Select(async session =>
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            try { await session!.LogoutAsync(timeout.Token); } catch { /* Local credentials are cleared by the session. */ }
            session!.Dispose();
        }));
        _historyServerSession = null;
        _historyLocalSession = null;
    }
}
