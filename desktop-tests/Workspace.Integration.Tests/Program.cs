using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using AiReportDesktop;

internal static partial class Program
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

    [STAThread]
    private static int Main()
    {
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        app.Resources.MergedDictionaries.Add(new ResourceDictionary
        {
            Source = new Uri("/AiReportDesktop;component/BlueWhiteTheme.xaml", UriKind.Relative)
        });
        var failures = 0;
        app.Dispatcher.InvokeAsync(async () =>
        {
            try
            {
                await Run();
                Console.WriteLine("14 passed, 0 failed");
            }
            catch (Exception error)
            {
                failures++;
                Console.WriteLine("FAIL " + error);
            }
            finally { app.Shutdown(failures == 0 ? 0 : 1); }
        });
        app.Run();
        return failures == 0 ? 0 : 1;
    }

    private static void Check(bool value, string message)
    {
        if (!value) throw new InvalidOperationException(message);
    }
    private static object? Field(MainWindow window, string name) => typeof(MainWindow).GetField(name, Private)!.GetValue(window);
    private static void Set(MainWindow window, string name, object? value) => typeof(MainWindow).GetField(name, Private)!.SetValue(window, value);
    private static object? Call(MainWindow window, string name, params object?[] args) => typeof(MainWindow).GetMethod(name, Private)!.Invoke(window, args);
    private static Task CallAsync(MainWindow window, string name, params object?[] args) => (Task)Call(window, name, args)!;
    private static string BaselineLabel(object item) => (string)item.GetType().GetProperty("Label")!.GetValue(item)!;

    private static async Task Run()
    {
        var window = new MainWindow();
        Call(window, "InitializeAuthenticationView");
        Check(Field(window, "_memoryBaselineCombo") is ComboBox, "Existing memory baseline control was removed");
        Check(typeof(MainWindow).GetMethod("ExportRecordAsync", Private) != null, "HTML export was removed");
        Check(typeof(MainWindow).GetMethod("UsesSensitiveWordEvidence", BindingFlags.Static | BindingFlags.NonPublic) != null,
            "Sensitive-word precision support was removed");
        Check(typeof(MainWindow).GetMethod("SelectFunctionFindingsForDisplay", Private) == null,
            "Withdrawn sampling display was reintroduced");
        Check(window.WindowStyle == WindowStyle.None, "Application frame was removed");
        Console.WriteLine("PASS existing_memory_export_precision_and_frame_preserved");

        var firstApi = new WorkspaceHandler("first");
        using var first = new BackendSession(new Uri("http://127.0.0.1:19801/api"), firstApi);
        await first.LoginAsync("reviewer", "test-password");
        await CallAsync(window, "ActivateBackendSessionAsync", first);
        var baseline = (ComboBox)Field(window, "_memoryBaselineCombo")!;
        Check(baseline.Items.Cast<object>().Any(x => BaselineLabel(x).Contains("first")), "Baseline API was not connected after real authentication");
        Check((bool)typeof(MainWindow).GetProperty("CanUseActiveWorkspace", Private)!.GetValue(window)!, "Local authenticated review access failed");
        Check(firstApi.Calls.Any(x => x.Contains("/review-baselines")) && firstApi.Calls.Any(x => x == "/api/projects/tree"),
            "Existing project tree or memory requests were not authenticated");
        Console.WriteLine("PASS authenticated_local_workspace_loads_records_and_memory");

        firstApi.DelayBaselines = true;
        var staleBaseline = CallAsync(window, "LoadMemoryBaselinesAsync");
        var secondApi = new WorkspaceHandler("second");
        using var second = new BackendSession(new Uri("http://127.0.0.1:19802/api"), secondApi);
        await second.LoginAsync("reviewer", "test-password");
        Set(window, "_activeRecordId", "first-record");
        Set(window, "_activePreviewUrl", "http://127.0.0.1:19801/api/evaluate/old/preview");
        await CallAsync(window, "ActivateBackendSessionAsync", second);
        firstApi.CompleteDelayedBaseline();
        await staleBaseline;
        Check(baseline.Items.Cast<object>().Any(x => BaselineLabel(x).Contains("second")), "New backend baseline missing");
        Check(!baseline.Items.Cast<object>().Any(x => BaselineLabel(x).Contains("first")), "Stale backend overwrote memory baselines");
        Check(Field(window, "_activeRecordId") == null && Field(window, "_activePreviewUrl") == null, "Switch kept old record or preview");
        Console.WriteLine("PASS_backend_switch_discards_stale_memory_and_preview");

        await RunMemoryTests(window, second, secondApi);

        var restrictedApi = new WorkspaceHandler("server", restricted: true);
        using var restricted = new BackendSession(new Uri("http://server.test:19803/api"), restrictedApi);
        await restricted.LoginAsync("recipient", "test-password");
        await CallAsync(window, "ActivateBackendSessionAsync", restricted);
        Check(!(bool)typeof(MainWindow).GetProperty("CanUseActiveWorkspace", Private)!.GetValue(window)!, "Server recipient gained business access");
        Check(restrictedApi.Calls.All(x => !x.Contains("evaluate/")), "Restricted account executed workspace rules");
        Check(Field(window, "_historyServerSession") == null, "Unexpected server connection was opened");
        Console.WriteLine("PASS_server_recipient_keeps_inbox_without_review_access");

        var inbox = new HistoryInboxWindow(null, null);
        Check(inbox.WindowStyle == WindowStyle.None && inbox.Content is Border, "Inbox did not use current application frame");
        Check(inbox.FindResource("HistoryActionButton") is Style, "Inbox actions have no shared blue-white style");
        await CallAsync(window, "LogoutWorkspaceAsync");
        Check(!restricted.IsAuthenticated && Field(window, "_selectedProjectId") == null, "Logout retained a session or project");
        Set(window, "_activeRecordId", "stale-record");
        Call(window, "EnterWorkspaceWithoutLogin");
        Check(Field(window, "_activeRecordId") == null && baseline.Items.Count == 2,
            "Guest browsing retained old authenticated records or baselines");
        Check(!(bool)typeof(MainWindow).GetProperty("CanUseActiveWorkspace", Private)!.GetValue(window)!,
            "Guest browsing gained workspace access");
        Console.WriteLine("PASS_inbox_uses_shared_application_style");

        // No visible windows, private fixtures, servers, model API calls or real databases are used.
        ((DispatcherTimer)Field(window, "_completenessFileTimer")!).Stop();
        (Field(window, "_documentWebView") as IDisposable)?.Dispose();
        await restricted.LogoutAsync();
    }

    private sealed partial class WorkspaceHandler(string name, bool restricted = false) : HttpMessageHandler
    {
        public List<string> Calls { get; } = [];
        public bool DelayBaselines;
        private TaskCompletionSource<HttpResponseMessage>? _delayed;
        private HttpResponseMessage Json(object body, HttpStatusCode status = HttpStatusCode.OK) => new(status)
        {
            Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json")
        };
        public void CompleteDelayedBaseline() => _delayed!.TrySetResult(Baselines());
        private HttpResponseMessage Baselines() => Json(new { items = new[] { new { id = name + "-run", report_name = name + "报告", created_at = "2026-11-13T10:00:00" } } });
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            var path = request.RequestUri!.AbsolutePath;
            Calls.Add(path);
            if (path == "/api/auth/login")
                return Task.FromResult(Json(new { access_token = new string(name[0], 43), user = new { id = Guid.NewGuid(), username = name, role = "user" } }));
            Check(request.Headers.Authorization?.Scheme == "Bearer", "Workspace request omitted authentication");
            if (path.EndsWith("/auth/logout")) return Task.FromResult(new HttpResponseMessage(HttpStatusCode.NoContent));
            if (path == "/api/projects")
                return Task.FromResult(restricted ? Json(new { detail = "admin required" }, HttpStatusCode.Forbidden)
                    : Json(new[] { new { id = "shared-project-id", name = name + "项目" } }));
            if (path.EndsWith("/review-baselines"))
            {
                if (!DelayBaselines) return Task.FromResult(Baselines());
                _delayed = new(TaskCreationOptions.RunContinuationsAsynchronously);
                return _delayed.Task;
            }
            if (path == "/api/evaluate/rules")
                return Task.FromResult(Json(new { module_code = "function_correspondence", module_name = "对应关系", rules = Array.Empty<object>() }));
            if (path == "/api/projects/tree" || path == "/api/completeness/reports")
                return Task.FromResult(Json(new { items = Array.Empty<object>(), total = 0 }));
            if (path.StartsWith("/api/memory/")) return HandleMemoryAsync(request);
            throw new InvalidOperationException("Unexpected mock route " + path);
        }
    }
}
