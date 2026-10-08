"""Fictional SaaS dataset; expected answers are handwritten in expected.json."""

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1] / "data" / "business"
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / "orders.csv").write_text(
    "order_id,paid_at,paid_amount,refund_amount,region,product,status\n"
    "0001,2026-08-01,25000.00,0,华东,专业版,paid\n"
    "0002,2026-08-15,25000.00,0,华东,专业版,paid\n"
    "0003,2026-08-20,25000.00,0,华西,基础版,paid\n"
    "0004,2026-08-31,25000.00,0,华西,基础版,paid\n"
    "0005,2026-08-31T16:00:00Z,20000.00,1000.00,华东,专业版,refunded\n"
    "0006,2026-09-15,20000.00,0,华东,专业版,paid\n"
    "0007,2026-09-20,20000.00,3000.00,华西,基础版,refunded\n"
    "0008,2026-09-30,20000.00,0,华西,基础版,paid\n"
    "0009,,0,0,华东,专业版,cancelled\n",
    encoding="utf-8-sig",
)
(ROOT / "sales_summary.csv").write_text(
    "month,paid_sales,net_sales,paid_orders\n2026-08,100000.00,100000.00,4\n2026-09,80000.00,76000.00,4\n",
    encoding="utf-8-sig",
)
(ROOT / "feedback.csv").write_text(
    "feedback_id,date,text\n"
    "F001,2026-09-02,升级到3.2版本后经常闪退，无法完成操作，申请退款。\n"
    "F002,2026-09-05,更新后登录一直转圈，账户进不去，希望尽快修复。\n"
    "F003,2026-09-08,专业版价格太高，预算不足，准备取消续费。\n"
    "F004,2026-09-12,图表导出功能很好用，节省了整理报表的时间。\n"
    "F005,2026-09-16,基础版使用稳定，客服及时解决了发票问题。\n"
    "F006,2026-09-19,新版本运行时意外退出导致数据丢失，要求退还费用。\n",
    encoding="utf-8-sig",
)
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def pdf(name, pages):
    output = canvas.Canvas(str(ROOT / name), invariant=1, pageCompression=0)
    output.setTitle("虚构业务测试资料")
    for lines in pages:
        output.setFont("STSong-Light", 12)
        for line, text in enumerate(lines):
            output.drawString(48, 780 - 24 * line, text)
        output.showPage()
    output.save()


pdf(
    "quarterly_report.pdf",
    [
        [
            "虚构 SaaS 第三季度业务简报",
            "八月实付总额100000元，九月实付总额80000元，下降20%。",
            "九月累计退款4000元，按订单支付月份计算，净销售额76000元。",
            "报表和订单明细属于同一批业务，不能重复相加。",
        ],
        [
            "版本发布与质量反馈",
            "3.2版本于九月上线，新增图表导出和分析工作区。",
            "部分用户反馈升级后程序崩溃、闪退、意外退出，并提出退款。",
            "客服也收到无法登录的反馈，需要分别排查。",
            "这些时间重合和反馈不能直接证明版本导致销售下降。",
        ],
        [
            "经营建议与边界",
            "优先修复稳定性问题，随后评估价格与续费体验。",
            "华东和华西分别分析，避免把全部变化归因于单一区域。",
            "此数据集为人工构造，仅用于功能与检索测试。",
        ],
    ],
)
pdf(
    "product_manual.pdf",
    [
        [
            "虚构产品功能说明",
            "专业版提供图表导出、项目管理和分析工作区。",
            "基础版提供文件上传和预览，客服可协助处理发票。",
        ],
        [
            "故障排查说明",
            "遇到登录转圈时，请检查账号状态与网络。",
            "图表导出支持下载，订单与退款请联系业务支持。",
        ],
    ],
)
print("Generated fictional business fixtures in test/data/business.")
