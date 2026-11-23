using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using AiReportDesktop;

internal static partial class Program
{
    private static IEnumerable<DependencyObject> Nodes(DependencyObject root)
    {
        yield return root;
        foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>())
            foreach (var nested in Nodes(child)) yield return nested;
    }
    private static Button MemoryButton(DependencyObject root, string text) => Nodes(root).OfType<Button>().First(x => Equals(x.Content, text));
    private static string MemoryText(DependencyObject root) => string.Join("\n", Nodes(root).OfType<TextBlock>().Select(x => x.Text));
    private static JsonElement Element(object value) => JsonSerializer.SerializeToElement(value);
    private static async Task WaitFor(Func<bool> condition)
    {
        for (int i = 0; i < 100 && !condition(); ++i) await Task.Delay(10);
        Check(condition(), "Timed out waiting for mocked memory operation");
    }
    private static async Task<StackPanel> OpenMemory(MainWindow window, string id)
    {
        Set(window, "_activeRecordId", id);
        var root = new StackPanel();
        Call(window, "AddProjectMemoryPanel", root, id);
        await (Task)Field(window, "_memoryLoadTask")!;
        return root;
    }

    private static async Task RunMemoryTests(MainWindow window, BackendSession session, WorkspaceHandler api)
    {
        var panel = await OpenMemory(window, "result-1");
        Check(api.MemoryCalls.Count == 1 && api.MemoryCalls.All(x => x.Method == "GET"), "Opening memory started a paid or mutating request");
        Check(MemoryText(panel).Contains("旧历史重新检测后可跟踪"), "Server memory availability message was hidden");
        Check(MemoryText(panel).Contains("规则原结论：清单未提供") && MemoryText(panel).Contains("人工要求复核"),
            "Human feedback replaced or hid original findings");
        Console.WriteLine("PASS memory_open_is_read_only_and_preserves_original_findings");

        ((CheckBox)Field(window, "_llmReviewCheckBox")!).IsChecked = false;
        var action = MemoryButton(panel, "继续整理历史问题");
        api.DelayContinue = true;
        action.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        await WaitFor(() => api.ContinueCalls == 1);
        action.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(api.CreatedJobs == 1 && api.ContinueCalls == 1 && !action.IsEnabled, "Repeated clicks started duplicate jobs");
        using (var payload = JsonDocument.Parse(api.LastCreateBody!))
            Check(!payload.RootElement.GetProperty("use_llm").GetBoolean(), "Explicit no-LLM choice was ignored");
        api.CompleteContinue();
        await WaitFor(() => MemoryText(panel).Contains("已处理 2 / 2"));
        Check(api.MemoryCalls.Any(x => x.Method == "GET" && x.Path.EndsWith("/review-jobs/saved-job")), "Finished job was not read from persistent storage");
        Console.WriteLine("PASS memory_explicit_create_continue_is_single_flight_and_respects_model_choice");

        api.JobState = "needs_attention";
        var callsBefore = api.MemoryCalls.Count;
        var resumed = await OpenMemory(window, "result-1");
        Check(api.MemoryCalls.Skip(callsBefore).All(x => x.Method == "GET") && api.CreatedJobs == 1, "Reopening created another job");
        Check(MemoryText(resumed).Contains("存在失败项") && MemoryText(resumed).Contains("模型尚未配置"), "Failed/pending persisted state was hidden");
        var retry = Nodes(resumed).OfType<CheckBox>().First(x => x.Content?.ToString()?.Contains("同时重试失败项") == true);
        Check(retry.Visibility == Visibility.Visible && retry.IsChecked != true, "Failed items retried without explicit consent");
        retry.IsChecked = true;
        api.DelayContinue = false;
        MemoryButton(resumed, "继续整理历史问题").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        await WaitFor(() => api.ContinueCalls == 2 && MemoryText(resumed).Contains("已处理 2 / 2"));
        using (var body = JsonDocument.Parse(api.LastContinueBody!))
            Check(body.RootElement.GetProperty("retry_failed").GetBoolean() && !body.RootElement.GetProperty("skip_failed").GetBoolean(), "Retry request was not explicit");
        Check(api.CreatedJobs == 1, "Resuming used a new task");
        Console.WriteLine("PASS memory_reopen_resumes_saved_job_and_retries_failed_only_when_selected");

        ((CheckBox)Field(window, "_llmReviewCheckBox")!).IsChecked = true;
        var newRound = Nodes(resumed).OfType<CheckBox>().First(x => x.Content?.ToString()?.StartsWith("新建一轮整理") == true);
        newRound.IsChecked = true;
        var model = Nodes(resumed).OfType<CheckBox>().First(x => x.Content?.ToString()?.StartsWith("使用大模型辅助整理") == true);
        Check(model.IsEnabled && model.IsChecked == true && api.CreatedJobs == 1, "Choosing a new round started work or prevented choosing a model");
        newRound.IsChecked = false;
        Check(!model.IsEnabled && model.IsChecked == false && api.CreatedJobs == 1, "Cancelling a new round misrepresented the saved model option");
        newRound.IsChecked = true;
        MemoryButton(resumed, "开始新一轮整理").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        await WaitFor(() => api.CreatedJobs == 2 && api.ContinueCalls == 3 && MemoryText(resumed).Contains("已处理 2 / 2"));
        using (var body = JsonDocument.Parse(api.LastCreateBody!))
            Check(body.RootElement.GetProperty("use_llm").GetBoolean(), "New round did not accept the explicit model choice");
        Console.WriteLine("PASS completed_rule_only_job_can_start_explicit_new_model_round");

        api.JobState = "needs_attention";
        var failedPanel = await OpenMemory(window, "result-1");
        var retryChoice = Nodes(failedPanel).OfType<CheckBox>().First(x => x.Content?.ToString()?.StartsWith("同时重试失败项") == true);
        var skipChoice = Nodes(failedPanel).OfType<CheckBox>().First(x => x.Content?.ToString()?.StartsWith("跳过失败项") == true);
        retryChoice.IsChecked = true;
        skipChoice.IsChecked = true;
        Check(retryChoice.IsChecked == false, "Retry and skip are not mutually exclusive");
        MemoryButton(failedPanel, "继续整理历史问题").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        await WaitFor(() => api.ContinueCalls == 4 && MemoryText(failedPanel).Contains("已处理 2 / 2"));
        using (var body = JsonDocument.Parse(api.LastContinueBody!))
            Check(!body.RootElement.GetProperty("retry_failed").GetBoolean() && body.RootElement.GetProperty("skip_failed").GetBoolean(), "Skip was not explicit");
        Check(api.CreatedJobs == 2 && MemoryText(failedPanel).Contains("规则原结论"), "Skipping discarded original findings or created a new job");
        Console.WriteLine("PASS failed_auxiliary_items_can_be_explicitly_skipped_without_hiding_rules");

        var host = (StackPanel)Field(window, "_memoryResultHost")!;
        var generation = (int)Field(window, "_memoryPanelRevision")!;
        var revision = (int)Field(window, "_backendSessionRevision")!;
        var issue = Element(new { issue_id = "issue-1", title = "原始清单问题" });
        new WindowInteropHelper(window).EnsureHandle(); // hidden HWND permits owned dialogs without displaying the test window
        var feedbackDialog = (Window)Call(window, "BuildMemoryFeedbackDialog", "result-1", issue, host, session, revision, generation)!;
        var combo = Nodes(feedbackDialog).OfType<ComboBox>().Single();
        combo.SelectedIndex = 2; // resolved
        var inputs = Nodes(feedbackDialog).OfType<TextBox>().ToArray();
        Check(inputs.Length == 2 && inputs.All(x => x.MaxLength == 5000), "Feedback inputs do not match API bounds");
        inputs[0].Text = "已按要求补充";
        MemoryButton(feedbackDialog, "保存人工反馈").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(api.FeedbackWrites == 0 && MemoryText(feedbackDialog).Contains("需要填写可核验的证据"), "Resolved feedback saved without evidence");
        Check(feedbackDialog.WindowStyle == WindowStyle.None, "Feedback reintroduced a Windows frame");
        feedbackDialog.Close();
        Console.WriteLine("PASS feedback_requires_resolution_evidence_and_uses_shared_dialog_style");

        Set(window, "_selectedProjectId", "shared-project-id");
        var context = Element(new { facts = new[] { new { key = "建设范围", value = "本项目", source = "报告第二章" } }, aliases = new[] { new { canonical = "保障平台", variants = new[] { "本平台" } } } });
        var contextDialog = (Window)Call(window, "BuildProjectContextDialog", "shared-project-id", context, session, revision)!;
        var contextInputs = Nodes(contextDialog).OfType<TextBox>().ToArray();
        Check(contextInputs.Any(x => x.Text == "建设范围" && x.MaxLength == 80) && contextInputs.Any(x => x.Text == "本项目" && x.MaxLength == 2000), "Saved project context or limits were lost");
        Check(Nodes(contextDialog).OfType<ScrollViewer>().Any(x => x.VerticalScrollBarVisibility == ScrollBarVisibility.Auto), "Context dialog has no scrolling");
        contextInputs.First(x => x.Text == "本项目").Text = "";
        MemoryButton(contextDialog, "保存项目背景").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(api.ContextWrites == 0 && MemoryText(contextDialog).Contains("完整填写事项与内容"), "Invalid context was sent");
        Set(window, "_selectedProjectId", "different-project");
        MemoryButton(contextDialog, "保存项目背景").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(api.ContextWrites == 0 && MemoryText(contextDialog).Contains("当前项目或后端已切换"), "Stale context dialog wrote into another project");
        contextDialog.Close();
        Set(window, "_selectedProjectId", "shared-project-id");
        Console.WriteLine("PASS project_context_restores_editable_facts_aliases_and_blocks_invalid_or_stale_saves");

        var comparisons = new StackPanel();
        Call(window, "RenderRevisionReviews", comparisons, Element(new { revision_reviews = new[] { new { label = "部分整改", status = "partially_resolved", summary = "操作系统已补充，数据库仍缺", previous_evidence = "上版没有任何清单", current_evidence = "本版已列操作系统 3 套", reason = "仍有一项待补", remaining_requirements = new[] { "数据库数量" } } } }));
        var comparisonText = MemoryText(comparisons);
        Check(comparisonText.Contains("上版没有任何清单") && comparisonText.Contains("本版已列操作系统 3 套") && comparisonText.Contains("数据库数量"), "Before/after comparison lost evidence or remaining requirements");
        Console.WriteLine("PASS revision_comparison_shows_prior_current_evidence_and_remaining_requirements");

        api.DelayMemory = true;
        Set(window, "_activeRecordId", "stale-result");
        var oldHost = new StackPanel();
        Call(window, "AddProjectMemoryPanel", oldHost, "stale-result");
        var oldLoad = (Task)Field(window, "_memoryLoadTask")!;
        api.DelayMemory = false;
        var currentHost = await OpenMemory(window, "new-result");
        api.CompleteMemory();
        await oldLoad;
        Check(!MemoryText(oldHost).Contains("规则原结论") && MemoryText(currentHost).Contains("规则原结论"), "A stale result response repainted the old/new selection");
        api.DelayMemory = true;
        var sameHost = (StackPanel)Field(window, "_memoryResultHost")!;
        var sameGeneration = (int)Field(window, "_memoryPanelRevision")!;
        var olderRefresh = CallAsync(window, "LoadProjectMemoryAsync", "new-result", sameHost, sameGeneration);
        api.DelayMemory = false;
        api.RuleOpinion = "较新读取：已保留人工意见";
        await CallAsync(window, "LoadProjectMemoryAsync", "new-result", sameHost, sameGeneration);
        api.CompleteMemory("过时读取不应覆盖新意见");
        await olderRefresh;
        Check(MemoryText(currentHost).Contains("较新读取：已保留人工意见") && !MemoryText(currentHost).Contains("过时读取不应覆盖新意见"),
            "An older refresh overwrote a newer feedback read for the same result");
        api.RuleOpinion = "规则原结论：清单未提供";
        Call(window, "ResetProjectMemoryWorkspace");
        Check(Field(window, "_memoryResultHost") == null, "Workspace reset retained the memory host");
        Console.WriteLine("PASS memory_stale_result_and_workspace_reset_do_not_restore_old_feedback");
    }

    private sealed partial class WorkspaceHandler
    {
        public List<(string Method, string Path)> MemoryCalls = [];
        public int CreatedJobs, ContinueCalls, FeedbackWrites, ContextWrites;
        public bool DelayContinue, DelayMemory, SavedJob, UseModel;
        public string JobState = "paused";
        public string RuleOpinion = "规则原结论：清单未提供";
        public string? LastCreateBody, LastContinueBody;
        private TaskCompletionSource<HttpResponseMessage>? _continue, _memory;
        private object Job() => new { id = "saved-job", job_id = "saved-job", status = JobState, use_llm = UseModel,
            total = 2, completed = JobState == "completed" ? 2 : 0, failed = JobState == "needs_attention" ? 1 : 0, created_at = "2026-11-15T09:00:00Z",
            items = new[] { new { issue_id = "issue-1", title = "已有清单问题", status = JobState == "needs_attention" ? "failed" : JobState == "completed" ? "completed" : "pending", review_status = JobState == "needs_attention" ? "missing_api_key" : "rule_only", summary = "已保存整理说明", evidence = "表格原文" },
                new { issue_id = "issue-2", title = "待整理问题", status = JobState == "completed" ? "completed" : "pending", review_status = JobState == "completed" ? "rule_only" : "pending", summary = "", evidence = "正文原文" } } };
        private object Memory() => new { message = "旧历史重新检测后可跟踪", project_id = "shared-project-id", result_id = "result-1", issues = new[] { new { issue_id = "issue-1", label = "仍存在", status = "persistent", title = "原始清单问题", review_opinion = RuleOpinion, evidence = "第六章原文", verification = new { summary = "尚待核实" }, feedback = new { action = "needs_review", reason = "人工说明需再次核实", evidence = "原文清单", created_at = "2026-11-15" } } }, feedback = Array.Empty<object>(), jobs = SavedJob ? new[] { Job() } : Array.Empty<object>() };
        public void CompleteContinue() { JobState = "completed"; _continue!.TrySetResult(Json(Job())); }
        public void CompleteMemory(string? opinion = null)
        {
            var saved = RuleOpinion;
            if (opinion != null) RuleOpinion = opinion;
            _memory!.TrySetResult(Json(Memory()));
            RuleOpinion = saved;
        }
        private async Task<HttpResponseMessage> HandleMemoryAsync(HttpRequestMessage request)
        {
            var path = request.RequestUri!.AbsolutePath;
            MemoryCalls.Add((request.Method.Method, path));
            if (path.EndsWith("/review-jobs") && request.Method == HttpMethod.Post)
            {
                CreatedJobs++; SavedJob = true; LastCreateBody = await request.Content!.ReadAsStringAsync();
                using (var body = JsonDocument.Parse(LastCreateBody)) UseModel = body.RootElement.GetProperty("use_llm").GetBoolean();
                JobState = "paused";
                return Json(Job());
            }
            if (path.EndsWith("/continue"))
            {
                ContinueCalls++; LastContinueBody = await request.Content!.ReadAsStringAsync();
                if (DelayContinue) { _continue = new(TaskCreationOptions.RunContinuationsAsynchronously); return await _continue.Task; }
                JobState = "completed"; return Json(Job());
            }
            if (path.Contains("/review-jobs/")) return Json(Job());
            if (path.EndsWith("/feedback")) { FeedbackWrites++; return Json(new { id = "feedback-1" }); }
            if (path.EndsWith("/context")) { if (request.Method == HttpMethod.Put) ContextWrites++; return Json(new { facts = Array.Empty<object>(), aliases = Array.Empty<object>() }); }
            if (DelayMemory) { _memory = new(TaskCreationOptions.RunContinuationsAsynchronously); return await _memory.Task; }
            return Json(Memory());
        }
    }
}
