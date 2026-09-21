using Microsoft.Win32;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace AiReportDesktop;

public partial class MainWindow
{
    private string? CompletenessProjectId => (ProjectComboBox.SelectedItem as ProjectOption)?.Id;
    private StackPanel? _completenessHistory;
    private TextBlock? _completenessAttachmentsLabel;
    private Button? _completenessRunButton;
    private Button? _completenessSpecialistButton;
    private readonly List<string> _completenessAttachments = new();
    private bool _completenessRunning;
    private int _completenessGeneration;
    private int _completenessHistoryGeneration;
    private JsonElement? _latestCompleteness;
    private string? _completenessFingerprint;
    private readonly System.Windows.Threading.DispatcherTimer _completenessFileTimer = new() { Interval = TimeSpan.FromSeconds(3) };
    private bool _completenessFingerprintBusy;

    private Border BuildRealCompletenessPrecheck()
    {
        var root = new Grid();
        foreach (var width in new[] { 0.95, 1.15, 1.15 })
            root.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(width, GridUnitType.Star) });
        var left = new StackPanel();
        left.Children.Add(Text("前置完整性检查", 18, FontWeights.Bold, FindBrush("TextBrush")));
        left.Children.Add(Text("检查文件可读性、主体明显残缺及核心附表附件缺失。内容是否充分、正确由专项检测判断。", 13, null, FindBrush("MutedBrush"), new Thickness(0, 8, 0, 12), true));
        var attach = Button("选择附加材料", false);
        attach.Click += (_, _) =>
        {
            var dialog = new OpenFileDialog { Title = "选择本次完整性检查的附加材料", Multiselect = true, Filter = "材料|*.docx;*.pdf;*.txt;*.xlsx;*.xlsm|所有文件|*.*" };
            if (dialog.ShowDialog(this) != true) return;
            _completenessAttachments.Clear();
            _completenessAttachments.AddRange(dialog.FileNames);
            UpdateCompletenessAttachmentLabel();
            ResetCompletenessResult();
        };
        var clear = Button("清空附件", false);
        clear.Click += (_, _) => { _completenessAttachments.Clear(); UpdateCompletenessAttachmentLabel(); ResetCompletenessResult(); };
        left.Children.Add(ActionRow(attach, clear));
        _completenessAttachmentsLabel = Text("未选择附加材料；正文内嵌附件也会核查。", 12, null, FindBrush("MutedBrush"), new Thickness(0, 8, 0, 8), true);
        left.Children.Add(_completenessAttachmentsLabel);
        _completenessRunButton = Button("执行完整性检查", true);
        _completenessRunButton.Click += OnRunCompletenessCheckClicked;
        left.Children.Add(_completenessRunButton);
        root.Children.Add(Card(left, new Thickness(18, 16, 18, 16), new Thickness(0, 0, 12, 0)));

        var middle = new StackPanel();
        middle.Children.Add(Text("本次完整性检查结果", 18, FontWeights.Bold, FindBrush("TextBrush")));
        _completenessStatusTextBlock = Text("未检查", 14, FontWeights.SemiBold, Brush(181, 71, 8), new Thickness(0, 8, 0, 0));
        middle.Children.Add(_completenessStatusTextBlock);
        middle.Children.Add(CompletenessItem("报告文本", "未检查", Brush(181, 71, 8), t => _reportTextCompletenessTextBlock = t));
        middle.Children.Add(CompletenessItem("附件材料", "未检查", Brush(181, 71, 8), t => _attachmentCompletenessTextBlock = t));
        middle.Children.Add(CompletenessItem("表格数据", "未检查", Brush(181, 71, 8), t => _tableCompletenessTextBlock = t));
        middle.Children.Add(CompletenessItem("关键章节", "未检查", Brush(181, 71, 8), t => _chapterCompletenessTextBlock = t));
        _completenessSummaryTextBlock = Text("选择项目和报告后执行检查。", 12, null, FindBrush("MutedBrush"), new Thickness(0, 8, 0, 8), true);
        middle.Children.Add(_completenessSummaryTextBlock);
        var view = Button("查看本次完整报告", false);
        view.Click += OnViewCompletenessResultClicked;
        middle.Children.Add(view);
        _completenessSpecialistButton = Button("进行专项检查", true);
        _completenessSpecialistButton.Margin = new Thickness(0, 10, 0, 0);
        _completenessSpecialistButton.IsEnabled = false;
        _completenessSpecialistButton.ToolTip = "完成本次完整性检查后，携带当前项目和报告进入专项检查。";
        _completenessSpecialistButton.Click += (_, _) => ShowPage("Specialist");
        middle.Children.Add(_completenessSpecialistButton);
        var middleCard = Card(middle, new Thickness(18, 16, 18, 16), new Thickness(0, 0, 12, 0));
        Grid.SetColumn(middleCard, 1);
        root.Children.Add(middleCard);

        var right = new StackPanel();
        right.Children.Add(Text("完整性检查历史", 18, FontWeights.Bold, FindBrush("TextBrush")));
        var refresh = Button("刷新历史", false);
        refresh.Click += async (_, _) => await LoadCompletenessHistoryAsync();
        right.Children.Add(refresh);
        _completenessHistory = new StackPanel();
        _completenessHistory.Children.Add(Text("选择项目后查看历史报告。", 13, null, FindBrush("MutedBrush")));
        right.Children.Add(new ScrollViewer { Content = _completenessHistory, Height = 255, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled });
        var rightCard = Card(right, new Thickness(18, 16, 18, 16), new Thickness(0));
        Grid.SetColumn(rightCard, 2);
        root.Children.Add(rightCard);
        _completenessFileTimer.Tick += async (_, _) => await CheckCompletenessFileChangeAsync();
        _completenessFileTimer.Start();
        Closed += (_, _) => _completenessFileTimer.Stop();
        return new Border { Child = root, Margin = new Thickness(0, 16, 0, 0), Background = Brushes.Transparent };
    }

    private void UpdateCompletenessAttachmentLabel()
    {
        if (_completenessAttachmentsLabel != null)
            _completenessAttachmentsLabel.Text = _completenessAttachments.Count == 0 ? "未选择附加材料；正文内嵌附件也会核查。" : string.Join("\n", _completenessAttachments.Select(Path.GetFileName));
    }

    private void ResetCompletenessResult()
    {
        _completenessGeneration++;
        _isCompletenessChecked = false;
        _latestCompleteness = null;
        _completenessFingerprint = null;
        if (_completenessSpecialistButton != null) _completenessSpecialistButton.IsEnabled = false;
        foreach (var item in new[] { _completenessStatusTextBlock, _reportTextCompletenessTextBlock, _attachmentCompletenessTextBlock, _tableCompletenessTextBlock, _chapterCompletenessTextBlock })
            SetCompletenessText(item, "未检查", Brush(181, 71, 8));
        if (_completenessSummaryTextBlock != null) _completenessSummaryTextBlock.Text = "材料或项目已切换，请重新检查；历史报告保留。";
    }

    private static async Task<string> CompletenessFingerprintAsync(IEnumerable<string> paths)
    {
        var values = new List<string>();
        foreach (var path in paths)
        {
            using var stream = File.OpenRead(path);
            values.Add(path + ":" + Convert.ToHexString(await SHA256.HashDataAsync(stream)));
        }
        return string.Join("|", values);
    }

    private async Task CheckCompletenessFileChangeAsync()
    {
        if (_completenessFingerprint == null || _completenessFingerprintBusy || _completenessRunning) return;
        _completenessFingerprintBusy = true;
        var generation = _completenessGeneration;
        try
        {
            var value = await CompletenessFingerprintAsync(new[] { ReportPathTextBlock.Text }.Concat(_completenessAttachments).ToArray());
            if (generation == _completenessGeneration && value != _completenessFingerprint) ResetCompletenessResult();
        }
        catch { if (generation == _completenessGeneration) ResetCompletenessResult(); }
        finally { _completenessFingerprintBusy = false; }
    }

    private async void OnRunCompletenessCheckClicked(object sender, RoutedEventArgs e)
    {
        if (_completenessRunning) return;
        var project = CompletenessProjectId;
        var path = ReportPathTextBlock.Text;
        if (string.IsNullOrWhiteSpace(project) || !File.Exists(path))
        {
            MessageBox.Show(this, "请先选择项目和有效报告文件。", "完整性检查");
            return;
        }
        ResetCompletenessResult();
        var generation = _completenessGeneration;
        var paths = new[] { path }.Concat(_completenessAttachments).ToArray();
        _completenessRunning = true;
        if (_completenessRunButton != null) _completenessRunButton.IsEnabled = false;
        SetCompletenessText(_completenessStatusTextBlock, "检查中…", Brush(37, 99, 235));
        try
        {
            using var form = new MultipartFormDataContent();
            form.Add(new StringContent(project), "project_id");
            form.Add(new StringContent(Guid.NewGuid().ToString()), "request_id");
            var fingerprints = new List<string>();
            long total = 0;
            for (var i = 0; i < paths.Length; i++)
            {
                total += new FileInfo(paths[i]).Length;
                if (total > 100L * 1024 * 1024) throw new InvalidOperationException("本次材料总大小不能超过100MB。");
                var bytes = await File.ReadAllBytesAsync(paths[i]);
                fingerprints.Add(paths[i] + ":" + Convert.ToHexString(SHA256.HashData(bytes)));
                form.Add(new ByteArrayContent(bytes), i == 0 ? "file" : "attachments", Path.GetFileName(paths[i]));
            }
            using var response = await BackendClient.PostAsync($"{BackendBaseUrl}/completeness/reports", form);
            var body = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode) throw new InvalidOperationException(ExtractErrorMessage(body));
            using var document = JsonDocument.Parse(body);
            if (generation == _completenessGeneration && project == CompletenessProjectId)
            {
                _latestCompleteness = document.RootElement.Clone();
                _completenessFingerprint = string.Join("|", fingerprints);
                _isCompletenessChecked = true;
                RenderCompletenessSummary(_latestCompleteness.Value);
            }
            await LoadCompletenessHistoryAsync();
        }
        catch (Exception exc)
        {
            if (generation == _completenessGeneration)
            {
                SetCompletenessText(_completenessStatusTextBlock, "检查请求失败", Brush(181, 71, 8));
                if (_completenessSummaryTextBlock != null) _completenessSummaryTextBlock.Text = exc.Message + "。如请求超时，请刷新历史确认是否已保存报告。";
            }
        }
        finally
        {
            _completenessRunning = false;
            if (_completenessRunButton != null) _completenessRunButton.IsEnabled = true;
        }
    }

    private void RenderCompletenessSummary(JsonElement report)
    {
        if (_completenessSpecialistButton != null) _completenessSpecialistButton.IsEnabled = true;
        var color = GetString(report, "status") == "pass" ? Brush(2, 122, 72) : Brush(181, 71, 8);
        SetCompletenessText(_completenessStatusTextBlock, GetString(report, "label"), color);
        foreach (var item in report.GetProperty("items").EnumerateArray())
        {
            var target = GetString(item, "key") switch { "text" => _reportTextCompletenessTextBlock, "attachments" => _attachmentCompletenessTextBlock, "tables" => _tableCompletenessTextBlock, "chapters" => _chapterCompletenessTextBlock, _ => null };
            SetCompletenessText(target, GetString(item, "label"), GetString(item, "status") == "pass" ? Brush(2, 122, 72) : Brush(181, 71, 8));
        }
        if (_completenessSummaryTextBlock != null)
        {
            var gaps = report.GetProperty("details").EnumerateArray().Concat(report.GetProperty("items").EnumerateArray().Where(x => GetString(x, "key") is not "chapters" and not "attachments"))
                .Where(x => GetString(x, "status") is "missing" or "review").Select(x => GetString(x, "title")).Distinct().Take(3);
            var info = report.TryGetProperty("info_count", out var infoCount) ? infoCount.GetInt32() : 0;
            _completenessSummaryTextBlock.Text = GetString(report, "filename") + "\n" + (GetString(report, "status") == "pass" ? "未发现明显材料残缺。" : "需关注：" + string.Join("、", gaps))
                + (info > 0 ? $"\n一般提示{info}项，不影响总体结论。" : "");
        }
    }

    private async Task LoadCompletenessHistoryAsync(int offset = 0)
    {
        if (_completenessHistory == null) return;
        var project = CompletenessProjectId;
        var generation = ++_completenessHistoryGeneration;
        if (offset == 0) _completenessHistory.Children.Clear();
        if (string.IsNullOrWhiteSpace(project)) { _completenessHistory.Children.Add(Text("请先选择项目。", 13)); return; }
        try
        {
            using var response = await BackendClient.GetAsync($"{BackendBaseUrl}/completeness/reports?project_id={Uri.EscapeDataString(project)}&offset={offset}");
            var body = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode) throw new InvalidOperationException(ExtractErrorMessage(body));
            using var doc = JsonDocument.Parse(body);
            if (project != CompletenessProjectId || generation != _completenessHistoryGeneration) return;
            var entries = doc.RootElement.GetProperty("items").EnumerateArray().ToArray();
            foreach (var entry in entries)
            {
                var id = GetString(entry, "id");
                var name = GetString(entry, "filename");
                var when = DateTimeOffset.TryParse(GetString(entry, "created_at"), out var time) ? time.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss") : GetString(entry, "created_at");
                var status = GetString(entry, "label");
                var missing = entry.GetProperty("missing_count").GetInt32();
                var review = entry.GetProperty("review_count").GetInt32();
                var summary = status + (missing > 0 ? $" · 主要缺失{missing}项" : "") + (review > 0 ? $" · 待核实{review}项" : "");
                var info = entry.TryGetProperty("info_count", out var infoCount) ? infoCount.GetInt32() : 0;
                if (info > 0) summary += $" · 提示{info}项";
                var version = GetString(entry, "version");
                var button = Button("", false);
                button.HorizontalContentAlignment = HorizontalAlignment.Stretch;
                button.Content = Text(name + "\n" + when + "\n" + summary + "\n规则版本：" + version, 12, null, FindBrush("TextBrush"), null, true);
                button.ToolTip = "查看此时保存的完整性报告";
                button.Click += async (_, _) =>
                {
                    try
                    {
                        using var detail = await BackendClient.GetAsync($"{BackendBaseUrl}/completeness/reports/{id}?project_id={Uri.EscapeDataString(project)}");
                        detail.EnsureSuccessStatusCode();
                        using var json = JsonDocument.Parse(await detail.Content.ReadAsStringAsync());
                        ShowCompletenessReport(json.RootElement.Clone());
                    }
                    catch (Exception exc) { MessageBox.Show(this, "历史报告加载失败：" + exc.Message); }
                };
                _completenessHistory.Children.Add(button);
            }
            if (entries.Length == 0 && offset == 0) _completenessHistory.Children.Add(Text("暂无完整性检查历史。", 13));
            var next = offset + entries.Length;
            if (next < doc.RootElement.GetProperty("total").GetInt32())
            {
                var more = Button("加载更多", false);
                more.Click += async (_, _) => { _completenessHistory.Children.Remove(more); await LoadCompletenessHistoryAsync(next); };
                _completenessHistory.Children.Add(more);
            }
        }
        catch (Exception exc) { if (project == CompletenessProjectId && generation == _completenessHistoryGeneration) _completenessHistory.Children.Add(Text("历史加载失败：" + exc.Message, 12, null, null, null, true)); }
    }

    private async void OnViewCompletenessResultClicked(object sender, RoutedEventArgs e)
    {
        await CheckCompletenessFileChangeAsync();
        if (_latestCompleteness is JsonElement report) ShowCompletenessReport(report);
        else MessageBox.Show(this, "尚无本次检查报告，请执行检查或从历史记录中查看。", "完整性报告");
    }

    private void ShowCompletenessReport(JsonElement report)
    {
        var panel = new StackPanel { Margin = new Thickness(24) };
        var dialog = new Window { Owner = this, Title = "完整性检查报告", Width = 950, Height = 720, WindowStartupLocation = WindowStartupLocation.CenterOwner, Content = new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto } };
        panel.Children.Add(Text(GetString(report, "filename"), 20, FontWeights.Bold, null, null, true));
        panel.Children.Add(Text("结论：" + GetString(report, "label") + "　检查时间：" + GetString(report, "created_at"), 14, null, null, new Thickness(0, 10, 0, 10), true));
        panel.Children.Add(Text("规则版本：" + GetString(report, "version") + "。历史报告保留检查当时的判定尺度。", 12, null, FindBrush("MutedBrush"), null, true));
        if (report.TryGetProperty("info_count", out var infoCount) && infoCount.GetInt32() > 0)
            panel.Children.Add(Text($"一般提示{infoCount.GetInt32()}项，不影响总体结论。", 13, null, FindBrush("MutedBrush"), null, true));
        panel.Children.Add(Text(GetString(report, "scope"), 13, null, FindBrush("MutedBrush"), null, true));
        var project = GetString(report, "project_id");
        var id = GetString(report, "id");
        var export = Button("导出报告（HTML，可打印为PDF）", false);
        export.Click += async (_, _) => await SaveCompletenessDownloadAsync($"{BackendBaseUrl}/completeness/reports/{id}/export?project_id={Uri.EscapeDataString(project)}", "完整性检查报告.html", "HTML报告|*.html", dialog);
        panel.Children.Add(export);
        var index = 0;
        foreach (var source in report.GetProperty("inventory").EnumerateArray())
        {
            var n = index++;
            var filename = GetString(source, "filename");
            var download = Button("下载本次原文件：" + filename, false);
            download.Click += async (_, _) => await SaveCompletenessDownloadAsync($"{BackendBaseUrl}/completeness/reports/{id}/sources/{n}?project_id={Uri.EscapeDataString(project)}", filename, "原文件|*.*", dialog);
            panel.Children.Add(download);
            panel.Children.Add(Text("SHA256：" + GetString(source, "sha256"), 11, null, FindBrush("MutedBrush"), null, true));
        }
        foreach (var item in report.GetProperty("items").EnumerateArray().Concat(report.GetProperty("details").EnumerateArray()))
        {
            panel.Children.Add(Text(GetString(item, "title") + " · " + GetString(item, "label"), 16, FontWeights.SemiBold, null, new Thickness(0, 18, 0, 6), true));
            panel.Children.Add(Text(GetString(item, "reason"), 13, null, null, null, true));
            foreach (var evidence in item.GetProperty("evidence").EnumerateArray())
            {
                var location = string.Join(" · ", evidence.EnumerateObject().Where(x => x.Name is not "quote" and not "is_heading" and not "is_caption").Select(x => (x.Name switch { "filename" => "文件", "section" => "章节", "paragraph" => "正文段落", "table_index" => "表格", "table_path" => "嵌套表", "row_index" => "行", "page" => "页码", "line" => "文本行", "sheet" => "工作表", _ => x.Name }) + ": " + x.Value.ToString()));
                panel.Children.Add(Text(location, 11, null, FindBrush("MutedBrush"), new Thickness(0, 8, 0, 2), true));
                panel.Children.Add(new Border { Background = Brush(255, 250, 220), Padding = new Thickness(10), Child = Text(GetString(evidence, "quote"), 13, null, null, null, true) });
            }
            var advice = GetString(item, "advice");
            if (!string.IsNullOrWhiteSpace(advice)) panel.Children.Add(Text("建议：" + advice, 13, null, null, new Thickness(0, 6, 0, 0), true));
        }
        panel.Children.Add(Text(GetString(report, "disclaimer"), 12, null, FindBrush("MutedBrush"), new Thickness(0, 18, 0, 0), true));
        dialog.ShowDialog();
    }

    private async Task SaveCompletenessDownloadAsync(string url, string filename, string filter, Window owner)
    {
        var save = new SaveFileDialog { FileName = filename, Filter = filter };
        if (save.ShowDialog(owner) != true) return;
        try
        {
            using var response = await BackendClient.GetAsync(url);
            response.EnsureSuccessStatusCode();
            await File.WriteAllBytesAsync(save.FileName, await response.Content.ReadAsByteArrayAsync());
        }
        catch (Exception exc) { MessageBox.Show(owner, "保存失败：" + exc.Message); }
    }
}
