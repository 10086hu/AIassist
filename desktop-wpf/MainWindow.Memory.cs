using System.IO;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace AiReportDesktop;

public partial class MainWindow
{
    private int _memoryPanelRevision;
    private int _memoryReadRevision;
    private StackPanel? _memoryResultHost;
    private Task? _memoryLoadTask;
    private bool _projectContextBusy;
    private readonly HashSet<string> _memoryWrites = new(StringComparer.Ordinal);

    private sealed record MemoryChoice(string Code, string Label);

    private void ResetProjectMemoryWorkspace()
    {
        ++_memoryPanelRevision;
        ++_memoryReadRevision;
        _memoryResultHost = null;
        // In-flight requests retain their own locks until they finish; a
        // backend switch cannot accidentally unlock a different request.
    }

    private bool IsCurrentMemoryView(BackendSession session, int revision, string resultId, int generation) =>
        IsCurrentBackendSession(session, revision) && generation == _memoryPanelRevision && _activeRecordId == resultId;

    private void AddProjectMemoryPanel(Panel target, string resultId)
    {
        ++_memoryPanelRevision;
        var host = new StackPanel();
        _memoryResultHost = host;
        target.Children.Add(new Border
        {
            Background = Brush(248, 251, 255), BorderBrush = Brush(191, 219, 254),
            BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8),
            Padding = new Thickness(12), Margin = new Thickness(0, 4, 0, 14), Child = host
        });
        _memoryLoadTask = LoadProjectMemoryAsync(resultId, host, _memoryPanelRevision);
    }

    private async Task<JsonElement> MemoryJsonAsync(BackendSession session, HttpMethod method, string path, object? body = null)
    {
        using var request = new HttpRequestMessage(method, session.ResolveApiUri(path));
        if (body != null) request.Content = JsonContent.Create(body);
        using var response = await session.Client.SendAsync(request);
        await session.EnsureSuccessAsync(response, CancellationToken.None);
        using var document = await BackendSession.ReadJsonAsync(response, 8 * 1024 * 1024, CancellationToken.None);
        return document.RootElement.Clone();
    }

    private async Task LoadProjectMemoryAsync(string resultId, StackPanel host, int generation)
    {
        var session = ActiveBackendSession;
        var revision = _backendSessionRevision;
        var readRevision = ++_memoryReadRevision;
        bool IsCurrentRead() => readRevision == _memoryReadRevision && IsCurrentMemoryView(session, revision, resultId, generation);
        if (!CanUseActiveWorkspace || !IsCurrentRead()) return;
        host.Children.Clear();
        host.Children.Add(Text("整改跟踪与人工反馈", 15, FontWeights.SemiBold, FindBrush("TextBrush")));
        host.Children.Add(Text("正在读取已保存的整改记录…", 12, null, FindBrush("MutedBrush"), new Thickness(0, 7, 0, 0), true));
        try
        {
            var data = await MemoryJsonAsync(session, HttpMethod.Get, "memory/results/" + Uri.EscapeDataString(resultId));
            if (!IsCurrentRead()) return;
            var savedJob = LatestMemoryJob(data);
            string jobWarning = "";
            if (savedJob is JsonElement saved)
            {
                var jobId = FirstNonEmpty(GetString(saved, "id"), GetString(saved, "job_id"));
                if (jobId.Length > 0)
                {
                    try { savedJob = await MemoryJsonAsync(session, HttpMethod.Get, "memory/review-jobs/" + Uri.EscapeDataString(jobId)); }
                    catch (Exception error) { jobWarning = "已读取保存摘要，任务详情暂未刷新：" + error.Message; }
                }
            }
            if (!IsCurrentRead()) return;
            RenderProjectMemoryPanel(resultId, host, data, session, revision, generation, savedJob);
            if (jobWarning.Length > 0) host.Children.Add(Text(jobWarning, 12, null, Brush(181, 71, 8), null, true));
        }
        catch (Exception error)
        {
            if (!IsCurrentRead()) return;
            host.Children.Clear();
            host.Children.Add(Text("整改跟踪暂未加载：" + error.Message, 12, null, Brush(181, 71, 8), null, true));
            var retry = Button("重新读取整改记录", false);
            retry.Click += async (_, _) => await LoadProjectMemoryAsync(resultId, host, generation);
            host.Children.Add(retry);
        }
    }

    private void RenderProjectMemoryPanel(string resultId, StackPanel host, JsonElement data,
        BackendSession session, int revision, int generation, JsonElement? savedJob = null)
    {
        host.Children.Clear();
        host.Children.Add(Text("整改跟踪与人工反馈", 15, FontWeights.SemiBold, FindBrush("TextBrush")));
        host.Children.Add(Text("人工意见单独留存，不覆盖原检查结论。打开此区域只读取记录；继续整理需要主动点击。", 12,
            null, FindBrush("MutedBrush"), new Thickness(0, 6, 0, 10), true));
        AddTextIf(host, "跟踪说明", GetString(data, "message"));
        var refresh = Button("刷新整改记录", false);
        refresh.Click += async (_, _) => await LoadProjectMemoryAsync(resultId, host, generation);
        host.Children.Add(refresh);
        var issuePanel = new StackPanel();
        if (data.TryGetProperty("issues", out var issues) && issues.ValueKind == JsonValueKind.Array)
        {
            foreach (var issue in issues.EnumerateArray())
            {
                var issueId = GetString(issue, "issue_id");
                if (string.IsNullOrWhiteSpace(issueId)) continue;
                var row = new StackPanel();
                row.Children.Add(Text(FirstNonEmpty(GetString(issue, "title"), "历史问题") + " · "
                    + FirstNonEmpty(GetString(issue, "label"), GetString(issue, "status")), 13, FontWeights.SemiBold,
                    FindBrush("TextBrush"), null, true));
                AddTextIf(row, "检查意见", GetString(issue, "review_opinion"));
                AddTextIf(row, "原文依据", MemoryValue(issue, "evidence"));
                if (issue.TryGetProperty("verification", out var verification) && verification.ValueKind != JsonValueKind.Null)
                    AddTextIf(row, "核验说明", MemoryVerification(verification));
                if (issue.TryGetProperty("feedback", out var feedback) && feedback.ValueKind == JsonValueKind.Object)
                {
                    AddTextIf(row, "最新人工意见", FeedbackSummary(feedback), Brush(37, 99, 235));
                }
                var feedbackButton = Button("记录人工反馈", false);
                var issueCopy = issue.Clone();
                feedbackButton.Click += (_, _) =>
                {
                    if (!IsCurrentMemoryView(session, revision, resultId, generation)) return;
                    BuildMemoryFeedbackDialog(resultId, issueCopy, host, session, revision, generation).ShowDialog();
                };
                row.Children.Add(feedbackButton);
                issuePanel.Children.Add(new Border { Padding = new Thickness(10), Margin = new Thickness(0, 6, 0, 0),
                    BorderBrush = Brush(219, 229, 241), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(6),
                    Background = Brushes.White, Child = row });
            }
        }
        host.Children.Add(new Expander { Header = $"逐项核验与人工反馈（{issuePanel.Children.Count} 项）", Content = issuePanel,
            IsExpanded = issuePanel.Children.Count <= 5, Margin = new Thickness(0, 8, 0, 6) });
        if (data.TryGetProperty("feedback", out var history) && history.ValueKind == JsonValueKind.Array && history.GetArrayLength() > 0)
        {
            var historyPanel = new StackPanel();
            foreach (var item in history.EnumerateArray())
                historyPanel.Children.Add(Text(FeedbackSummary(item), 12, null, FindBrush("MutedBrush"), new Thickness(0, 4, 0, 5), true));
            host.Children.Add(new Expander { Header = $"人工反馈历史（{history.GetArrayLength()} 条，含撤销记录）", Content = historyPanel });
        }
        var jobArea = new StackPanel { Margin = new Thickness(0, 10, 0, 0) };
        host.Children.Add(jobArea);
        RenderMemoryReviewJob(resultId, host, jobArea, savedJob ?? LatestMemoryJob(data), session, revision, generation);
    }

    private void AddTextIf(StackPanel panel, string label, string value) => AddTextIf(panel, label, value, FindBrush("TextBrush"));

    private static JsonElement? LatestMemoryJob(JsonElement data)
    {
        if (!data.TryGetProperty("jobs", out var jobs) || jobs.ValueKind != JsonValueKind.Array) return null;
        return jobs.EnumerateArray().Where(x => x.ValueKind == JsonValueKind.Object)
            .OrderByDescending(x => GetString(x, "created_at"), StringComparer.Ordinal).Select(x => (JsonElement?)x.Clone()).FirstOrDefault();
    }

    private static string MemoryValue(JsonElement data, string key)
    {
        if (!data.TryGetProperty(key, out var value) || value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined) return "";
        return value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : value.ToString();
    }

    private static string MemoryVerification(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.String) return value.GetString() ?? "";
        if (value.ValueKind != JsonValueKind.Object) return value.ToString();
        return string.Join("；", new[] { GetString(value, "label"), GetString(value, "summary"), GetString(value, "reason"),
            GetString(value, "status") }.Where(x => !string.IsNullOrWhiteSpace(x)).Distinct());
    }

    private static string FeedbackSummary(JsonElement feedback)
    {
        var action = GetString(feedback, "action") switch
        {
            "false_positive" => "人工标记误报", "resolved" => "人工确认已整改", "not_applicable" => "人工标记不适用",
            "needs_review" => "人工要求复核", "revoke" => "撤销此前人工意见", var other => other
        };
        return string.Join(" · ", new[] { action, GetString(feedback, "reason"), GetString(feedback, "evidence"),
            GetString(feedback, "actor"), GetString(feedback, "created_at").Replace('T', ' ') }.Where(x => !string.IsNullOrWhiteSpace(x)));
    }

    private void RenderMemoryReviewJob(string resultId, StackPanel host, StackPanel area, JsonElement? job,
        BackendSession session, int revision, int generation)
    {
        area.Children.Clear();
        area.Children.Add(Text("历史问题整理", 14, FontWeights.SemiBold, FindBrush("TextBrush")));
        var useLlm = new CheckBox { Content = "使用大模型辅助整理（会调用已配置的模型）", IsChecked = _llmReviewCheckBox?.IsChecked == true,
            Margin = new Thickness(0, 7, 0, 7) };
        string jobId = "";
        var finished = false;
        var failed = false;
        var running = false;
        var savedUseLlm = useLlm.IsChecked == true;
        if (job is JsonElement saved)
        {
            jobId = FirstNonEmpty(GetString(saved, "id"), GetString(saved, "job_id"));
            finished = GetString(saved, "status") is "completed" or "done";
            running = GetString(saved, "status") == "running";
            failed = GetInt(saved, "failed") > 0 || (saved.TryGetProperty("items", out var failureItems)
                && failureItems.ValueKind == JsonValueKind.Array && failureItems.EnumerateArray().Any(x => GetString(x, "status") == "failed"));
            if (saved.TryGetProperty("use_llm", out var mode)) useLlm.IsChecked = mode.ValueKind == JsonValueKind.True;
            savedUseLlm = useLlm.IsChecked == true;
            useLlm.IsEnabled = false;
            area.Children.Add(Text($"已保存任务：{MemoryJobStatus(GetString(saved, "status"))} · 已处理 {GetInt(saved, "completed")} / {GetInt(saved, "total")}",
                12, null, FindBrush("MutedBrush"), new Thickness(0, 6, 0, 3), true));
            if (saved.TryGetProperty("items", out var items) && items.ValueKind == JsonValueKind.Array)
            {
                var itemPanel = new StackPanel();
                foreach (var item in items.EnumerateArray())
                {
                    var state = FirstNonEmpty(GetString(item, "review_status"), GetString(item, "status"));
                    itemPanel.Children.Add(Text(FirstNonEmpty(GetString(item, "title"), "历史问题") + " · " + MemoryJobStatus(state),
                        12, FontWeights.SemiBold, FindBrush("TextBrush"), new Thickness(0, 8, 0, 3), true));
                    AddTextIf(itemPanel, "整理意见", GetString(item, "summary"));
                    AddTextIf(itemPanel, "依据", MemoryValue(item, "evidence"));
                    AddTextIf(itemPanel, "提示", FirstNonEmpty(GetString(item, "error"), GetString(item, "reason")));
                }
                area.Children.Add(new Expander { Header = "查看已整理、失败和待处理项", Content = itemPanel, Margin = new Thickness(0, 5, 0, 3) });
            }
        }
        var newRound = new CheckBox { Content = "新建一轮整理（保留已有任务，可重新选择是否使用模型）",
            Visibility = jobId.Length > 0 ? Visibility.Visible : Visibility.Collapsed, IsEnabled = !running,
            Margin = new Thickness(0, 7, 0, 2) };
        area.Children.Add(newRound);
        area.Children.Add(useLlm);
        area.Children.Add(Text(jobId.Length > 0 ? "继续使用已保存任务的模型选项。每次最多整理 5 项，原规则结果保持可见。"
            : "不勾选时仅按已有证据整理；勾选时使用当前后端模型。每次最多处理 5 项。", 12,
            null, FindBrush("MutedBrush"), null, true));
        var retryFailed = new CheckBox { Content = "同时重试失败项（使用已保存任务的模型选项）", Margin = new Thickness(0, 7, 0, 2), Visibility = failed ? Visibility.Visible : Visibility.Collapsed };
        area.Children.Add(retryFailed);
        var skipFailed = new CheckBox { Content = "跳过失败项，保留原检查结论与证据", Margin = new Thickness(0, 4, 0, 2), Visibility = failed ? Visibility.Visible : Visibility.Collapsed };
        area.Children.Add(skipFailed);
        retryFailed.Checked += (_, _) => skipFailed.IsChecked = false;
        skipFailed.Checked += (_, _) => retryFailed.IsChecked = false;
        var action = Button(finished ? "历史问题已整理完成" : running ? "整理任务正在执行，请刷新查看" : "继续整理历史问题", true);
        action.Margin = new Thickness(0, 7, 0, 0);
        action.IsEnabled = !finished && !running;
        var status = Text("", 12, null, Brush(181, 71, 8), new Thickness(0, 7, 0, 0), true);
        area.Children.Add(action);
        area.Children.Add(status);
        void UpdateRoundChoice()
        {
            var create = newRound.IsChecked == true;
            useLlm.IsEnabled = create || jobId.Length == 0;
            useLlm.IsChecked = create ? _llmReviewCheckBox?.IsChecked == true : savedUseLlm;
            retryFailed.IsEnabled = skipFailed.IsEnabled = !create;
            if (create) { retryFailed.IsChecked = false; skipFailed.IsChecked = false; }
            action.Content = create ? "开始新一轮整理" : finished ? "历史问题已整理完成" : running ? "整理任务正在执行，请刷新查看" : "继续整理历史问题";
            action.IsEnabled = create || (!finished && !running);
        }
        newRound.Checked += (_, _) => UpdateRoundChoice();
        newRound.Unchecked += (_, _) => UpdateRoundChoice();
        action.Click += async (_, _) =>
        {
            if (!IsCurrentMemoryView(session, revision, resultId, generation)) return;
            var key = session.ApiBase + "/" + resultId + "/review-job";
            if (!_memoryWrites.Add(key)) return;
            action.IsEnabled = false;
            useLlm.IsEnabled = false;
            retryFailed.IsEnabled = skipFailed.IsEnabled = newRound.IsEnabled = false;
            status.Text = "正在整理，结果会保存到当前任务；请稍候…";
            try
            {
                if (string.IsNullOrWhiteSpace(jobId) || newRound.IsChecked == true)
                {
                    savedUseLlm = useLlm.IsChecked == true;
                    var created = await MemoryJsonAsync(session, HttpMethod.Post, "memory/results/" + Uri.EscapeDataString(resultId) + "/review-jobs",
                        new { use_llm = useLlm.IsChecked == true });
                    jobId = FirstNonEmpty(GetString(created, "id"), GetString(created, "job_id"));
                    if (string.IsNullOrWhiteSpace(jobId)) throw new InvalidDataException("后端未返回整理任务编号，请刷新记录。");
                    finished = false; running = false;
                    newRound.IsChecked = false;
                    // The choice changed while the new task was being created. Keep
                    // every action locked until its first batch has returned.
                    action.IsEnabled = false; useLlm.IsEnabled = false;
                    retryFailed.IsEnabled = skipFailed.IsEnabled = false;
                }
                if (!IsCurrentMemoryView(session, revision, resultId, generation)) return;
                var updated = await MemoryJsonAsync(session, HttpMethod.Post, "memory/review-jobs/" + Uri.EscapeDataString(jobId) + "/continue",
                    new { batch_size = 5, retry_failed = retryFailed.IsChecked == true, skip_failed = skipFailed.IsChecked == true });
                if (!IsCurrentMemoryView(session, revision, resultId, generation)) return;
                // Re-read the durable job, so reopening the application shows the same task.
                var durable = await MemoryJsonAsync(session, HttpMethod.Get, "memory/review-jobs/" + Uri.EscapeDataString(jobId));
                if (IsCurrentMemoryView(session, revision, resultId, generation))
                {
                    if (host.Children.Contains(area)) RenderMemoryReviewJob(resultId, host, area, durable, session, revision, generation);
                    else await LoadProjectMemoryAsync(resultId, host, generation);
                }
            }
            catch (Exception error)
            {
                if (IsCurrentMemoryView(session, revision, resultId, generation))
                {
                    status.Text = "整理未完成：" + error.Message + "。任务进度已保留，可刷新后继续。";
                    action.IsEnabled = true;
                    retryFailed.IsEnabled = skipFailed.IsEnabled = newRound.IsEnabled = true;
                    useLlm.IsEnabled = string.IsNullOrWhiteSpace(jobId);
                }
            }
            finally { _memoryWrites.Remove(key); }
        };
    }

    private static string MemoryJobStatus(string value) => value switch
    {
        "pending" => "待处理", "running" => "处理中", "completed" or "done" or "reviewed" => "已整理",
        "failed" or "error" => "整理失败", "skipped" => "已跳过辅助整理", "partial" or "paused" => "进度已保存，待继续",
        "needs_attention" => "存在失败项，可选择重试", "rule_only" => "已按规则证据整理", "interrupted" => "上次执行中断，待重试",
        "missing_api_key" => "模型尚未配置", "unavailable" => "模型暂不可用", _ => "辅助整理未完成"
    };

    private AppDialog BuildMemoryFeedbackDialog(string resultId, JsonElement issue, StackPanel host,
        BackendSession session, int revision, int generation)
    {
        var dialog = new AppDialog("人工反馈", "人工意见会保留历史，原检查结论与原文依据保持可见。", "✓") { Owner = this, Width = 640 };
        var panel = new StackPanel();
        panel.Children.Add(Text(FirstNonEmpty(GetString(issue, "title"), "历史问题"), 14, FontWeights.SemiBold, FindBrush("TextBrush"), null, true));
        var choices = new[] { new MemoryChoice("needs_review", "需要再次核实"), new MemoryChoice("false_positive", "疑似误报"),
            new MemoryChoice("resolved", "已整改（需填写证据）"), new MemoryChoice("not_applicable", "本项目不适用"), new MemoryChoice("revoke", "撤销此前人工意见") };
        var choice = new ComboBox { ItemsSource = choices, DisplayMemberPath = "Label", SelectedIndex = 0, Margin = new Thickness(0, 10, 0, 10) };
        panel.Children.Add(choice);
        panel.Children.Add(Text("说明理由", 13, FontWeights.SemiBold, FindBrush("TextBrush")));
        var reason = MemoryInput(dialog, 78); reason.MaxLength = 5000; panel.Children.Add(reason);
        panel.Children.Add(Text("证据或整改说明（确认已整改时必填）", 13, FontWeights.SemiBold, FindBrush("TextBrush")));
        var evidence = MemoryInput(dialog, 90); evidence.MaxLength = 5000; panel.Children.Add(evidence);
        var errorText = Text("", 12, null, Brush(181, 71, 8), new Thickness(0, 6, 0, 0), true); panel.Children.Add(errorText);
        var save = new Button { Content = "保存人工反馈", Style = (Style)dialog.FindResource("DialogPrimaryButton"), IsDefault = true };
        save.Click += async (_, _) =>
        {
            if (!IsCurrentMemoryView(session, revision, resultId, generation)) { errorText.Text = "当前项目或后端已切换，请重新打开反馈。"; return; }
            var selected = (MemoryChoice)choice.SelectedItem;
            if (string.IsNullOrWhiteSpace(reason.Text)) { errorText.Text = "请填写理由。"; return; }
            if (selected.Code == "resolved" && string.IsNullOrWhiteSpace(evidence.Text)) { errorText.Text = "确认已整改需要填写可核验的证据或修改说明。"; return; }
            var key = session.ApiBase + "/" + resultId + "/" + GetString(issue, "issue_id") + "/feedback";
            if (!_memoryWrites.Add(key)) return;
            save.IsEnabled = false; dialog.CanDismiss = false;
            try
            {
                await MemoryJsonAsync(session, HttpMethod.Post, "memory/results/" + Uri.EscapeDataString(resultId) + "/feedback",
                    new { issue_id = GetString(issue, "issue_id"), action = selected.Code, reason = reason.Text.Trim(), evidence = evidence.Text.Trim() });
                dialog.CanDismiss = true;
                dialog.DialogResult = true;
                if (IsCurrentMemoryView(session, revision, resultId, generation))
                    await LoadProjectMemoryAsync(resultId, host, generation);
            }
            catch (Exception error) { errorText.Text = "保存失败：" + error.Message; }
            finally { _memoryWrites.Remove(key); save.IsEnabled = true; dialog.CanDismiss = true; }
        };
        dialog.DialogBody.Content = panel;
        dialog.DialogFooter.Content = MemoryDialogActions(dialog, save);
        return dialog;
    }

    private static TextBox MemoryInput(AppDialog dialog, double height = 42) => new()
    {
        Style = (Style)dialog.FindResource("DialogInput"), Height = height, AcceptsReturn = height > 45,
        TextWrapping = TextWrapping.Wrap, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Margin = new Thickness(0, 5, 0, 10)
    };

    private static StackPanel MemoryDialogActions(AppDialog dialog, Button save)
    {
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var cancel = new Button { Content = "取消", Style = (Style)dialog.FindResource("DialogSecondaryButton"), Margin = new Thickness(0, 0, 10, 0) };
        cancel.Click += (_, _) => { if (dialog.CanDismiss) dialog.Close(); };
        actions.Children.Add(cancel); actions.Children.Add(save); return actions;
    }

    private async Task EditProjectContextAsync()
    {
        if (_projectContextBusy || !RequireWorkspaceDataAccess()) return;
        var projectId = _selectedProjectId;
        if (string.IsNullOrWhiteSpace(projectId))
        {
            AppMessageBox.Show(this, "请先选择项目。", "项目背景", MessageBoxButton.OK, MessageBoxImage.Warning); return;
        }
        var session = ActiveBackendSession; var revision = _backendSessionRevision;
        _projectContextBusy = true;
        try
        {
            var context = await MemoryJsonAsync(session, HttpMethod.Get, "memory/projects/" + Uri.EscapeDataString(projectId) + "/context");
            if (!IsCurrentBackendSession(session, revision) || projectId != _selectedProjectId) return;
            BuildProjectContextDialog(projectId, context, session, revision).ShowDialog();
        }
        catch (Exception error)
        {
            if (IsCurrentBackendSession(session, revision) && projectId == _selectedProjectId)
                AppMessageBox.Show(this, "项目背景加载失败：" + error.Message, "项目背景", MessageBoxButton.OK, MessageBoxImage.Error);
        }
        finally { _projectContextBusy = false; }
    }

    private AppDialog BuildProjectContextDialog(string projectId, JsonElement context, BackendSession session, int revision)
    {
        var dialog = new AppDialog("项目背景与名称对应", "只用于当前项目的上下文和名称匹配；每项说明保留来源，现有检查规则保持不变。", "≡") { Owner = this, Width = 780 };
        var content = new StackPanel();
        content.Children.Add(Text("项目背景", 15, FontWeights.SemiBold, FindBrush("TextBrush")));
        content.Children.Add(Text("填写项目类型、建设范围等已确认事实。来源可填写报告章节或确认说明。", 12, null, FindBrush("MutedBrush"), null, true));
        var factsPanel = new StackPanel(); content.Children.Add(factsPanel);
        var factRows = new List<(TextBox Key, TextBox Value, TextBox Source, FrameworkElement Row)>();
        void AddFact(string key = "", string value = "", string source = "")
        {
            if (factRows.Count >= 50) return;
            var row = new StackPanel();
            var keyInput = MemoryInput(dialog); keyInput.MaxLength = 80; keyInput.Text = key; keyInput.ToolTip = "事项名称，如项目类型";
            var valueInput = MemoryInput(dialog, 65); valueInput.MaxLength = 2000; valueInput.Text = value; valueInput.ToolTip = "事实内容";
            var sourceInput = MemoryInput(dialog); sourceInput.MaxLength = 200; sourceInput.Text = source; sourceInput.ToolTip = "来源章节或确认说明";
            row.Children.Add(Text("事项 / 内容 / 来源", 12, null, FindBrush("MutedBrush"), new Thickness(0, 8, 0, 0)));
            row.Children.Add(keyInput); row.Children.Add(valueInput); row.Children.Add(sourceInput);
            var remove = Button("移除此项", false); row.Children.Add(remove);
            var entry = (keyInput, valueInput, sourceInput, (FrameworkElement)row); factRows.Add(entry);
            remove.Click += (_, _) => { factRows.Remove(entry); factsPanel.Children.Remove(row); };
            factsPanel.Children.Add(row);
        }
        if (context.TryGetProperty("facts", out var facts) && facts.ValueKind == JsonValueKind.Array)
            foreach (var fact in facts.EnumerateArray()) AddFact(GetString(fact, "key"), GetString(fact, "value"), GetString(fact, "source"));
        var addFact = Button("添加背景事项", false); addFact.Click += (_, _) => AddFact(); content.Children.Add(addFact);
        content.Children.Add(Text("名称对应", 15, FontWeights.SemiBold, FindBrush("TextBrush"), new Thickness(0, 16, 0, 4)));
        content.Children.Add(Text("例如将“警务保障平台”和“本平台”关联到同一规范名称。不会合并不同项目。", 12, null, FindBrush("MutedBrush"), null, true));
        var aliasesPanel = new StackPanel(); content.Children.Add(aliasesPanel);
        var aliasRows = new List<(TextBox Canonical, TextBox Variants, FrameworkElement Row)>();
        void AddAlias(string canonical = "", string variants = "")
        {
            if (aliasRows.Count >= 50) return;
            var row = new StackPanel();
            row.Children.Add(Text("规范名称 / 别名（每行一个）", 12, null, FindBrush("MutedBrush"), new Thickness(0, 8, 0, 0)));
            var name = MemoryInput(dialog); name.MaxLength = 200; name.Text = canonical;
            var names = MemoryInput(dialog, 70); names.Text = variants;
            row.Children.Add(name); row.Children.Add(names);
            var remove = Button("移除此组", false); row.Children.Add(remove);
            var entry = (name, names, (FrameworkElement)row); aliasRows.Add(entry);
            remove.Click += (_, _) => { aliasRows.Remove(entry); aliasesPanel.Children.Remove(row); }; aliasesPanel.Children.Add(row);
        }
        if (context.TryGetProperty("aliases", out var aliases) && aliases.ValueKind == JsonValueKind.Array)
            foreach (var alias in aliases.EnumerateArray())
                AddAlias(GetString(alias, "canonical"), alias.TryGetProperty("variants", out var variants) && variants.ValueKind == JsonValueKind.Array
                    ? string.Join("\n", variants.EnumerateArray().Select(v => v.GetString())) : "");
        var addAlias = Button("添加名称对应", false); addAlias.Click += (_, _) => AddAlias(); content.Children.Add(addAlias);
        var errorText = Text("", 12, null, Brush(181, 71, 8), new Thickness(0, 8, 0, 0), true); content.Children.Add(errorText);
        var save = new Button { Content = "保存项目背景", Style = (Style)dialog.FindResource("DialogPrimaryButton"), IsDefault = true };
        save.Click += async (_, _) =>
        {
            if (!IsCurrentBackendSession(session, revision) || projectId != _selectedProjectId)
            { errorText.Text = "当前项目或后端已切换，请重新打开项目背景。"; return; }
            if (factRows.Any(x => string.IsNullOrWhiteSpace(x.Key.Text) || string.IsNullOrWhiteSpace(x.Value.Text)))
            { errorText.Text = "每项背景请完整填写事项与内容；来源未填时记为人工补充，空白项可以移除。"; return; }
            if (aliasRows.Any(x => string.IsNullOrWhiteSpace(x.Canonical.Text) || string.IsNullOrWhiteSpace(x.Variants.Text)))
            { errorText.Text = "每组名称请填写规范名称及至少一个别名。"; return; }
            if (aliasRows.Select(x => x.Canonical.Text.Trim()).Distinct(StringComparer.Ordinal).Count() != aliasRows.Count)
            { errorText.Text = "规范名称不能重复，请合并同一名称下的别名。"; return; }
            if (aliasRows.Any(x => x.Variants.Text.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).Distinct().Count() > 20))
            { errorText.Text = "每组最多填写 20 个别名。"; return; }
            if (aliasRows.Any(x => x.Variants.Text.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).Any(v => v.Length > 200)))
            { errorText.Text = "每个别名最多填写 200 个字符。"; return; }
            var key = session.ApiBase + "/" + projectId + "/context";
            if (!_memoryWrites.Add(key)) return;
            save.IsEnabled = false; dialog.CanDismiss = false;
            try
            {
                await MemoryJsonAsync(session, HttpMethod.Put, "memory/projects/" + Uri.EscapeDataString(projectId) + "/context", new
                {
                    facts = factRows.Select(x => new { key = x.Key.Text.Trim(), value = x.Value.Text.Trim(), source = x.Source.Text.Trim() }).ToArray(),
                    aliases = aliasRows.Select(x => new { canonical = x.Canonical.Text.Trim(), variants = x.Variants.Text.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).Distinct().ToArray() }).ToArray()
                });
                dialog.CanDismiss = true; dialog.DialogResult = true;
            }
            catch (Exception error) { errorText.Text = "保存失败：" + error.Message; }
            finally { _memoryWrites.Remove(key); save.IsEnabled = true; dialog.CanDismiss = true; }
        };
        dialog.DialogBody.Content = new ScrollViewer { Content = content, MaxHeight = 470, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        dialog.DialogFooter.Content = MemoryDialogActions(dialog, save);
        return dialog;
    }

    private void RenderRevisionReviews(Panel target, JsonElement memory)
    {
        if (!memory.TryGetProperty("revision_reviews", out var reviews) || reviews.ValueKind != JsonValueKind.Array || reviews.GetArrayLength() == 0) return;
        var comparison = new StackPanel();
        foreach (var review in reviews.EnumerateArray())
        {
            var row = new StackPanel();
            row.Children.Add(Text(FirstNonEmpty(GetString(review, "label"), GetString(review, "status")), 13, FontWeights.SemiBold, FindBrush("TextBrush"), null, true));
            AddTextIf(row, "修订说明", GetString(review, "summary"));
            AddTextIf(row, "上次依据", MemoryValue(review, "previous_evidence"));
            AddTextIf(row, "本次依据", MemoryValue(review, "current_evidence"));
            AddTextIf(row, "核验理由", GetString(review, "reason"));
            if (review.TryGetProperty("remaining_requirements", out var requirements) && requirements.ValueKind == JsonValueKind.Array)
                AddTextIf(row, "仍需补充", string.Join("；", requirements.EnumerateArray().Select(x => x.GetString())));
            comparison.Children.Add(new Border { Child = row, Padding = new Thickness(9), Margin = new Thickness(0, 5, 0, 4), Background = Brushes.White, CornerRadius = new CornerRadius(6) });
        }
        target.Children.Add(new Expander { Header = $"查看逐项修订前后对照（{reviews.GetArrayLength()} 项）", Content = comparison, Margin = new Thickness(0, 8, 0, 5) });
    }
}
