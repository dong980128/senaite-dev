# -*- coding: utf-8 -*-
from AccessControl import ClassSecurityInfo
from bika.lims import bikaMessageFactory as _
from Products.Archetypes.Registry import registerField
from senaite.core.browser.fields.record import RecordField
from Products.Archetypes import atapi

# 公用文本标签字段类
class LabelField(atapi.StringField):
    _properties = atapi.StringField._properties.copy()
    _properties.update({
        "type": "label",
        "widget": atapi.StringWidget(
            label=_("Label Field"),
            description=_("Enter text label")
        ),
    })

# 日期字段类
class DateField(atapi.DateTimeField):
    _properties = atapi.DateTimeField._properties.copy()
    _properties.update({
        "type": "date",
        "widget": atapi.CalendarWidget(
            label=_("Date Field"),
            description=_("Select a date")
        ),
    })

# 布尔值字段类
class BooleanField(atapi.BooleanField):
    _properties = atapi.BooleanField._properties.copy()
    _properties.update({
        "type": "boolean",
        "widget": atapi.BooleanWidget(
            label=_("Boolean Field"),
            description=_("Select Yes or No")
        ),
    })

# 整数字段类
class IntegerField(atapi.IntegerField):
    _properties = atapi.IntegerField._properties.copy()
    _properties.update({
        "type": "int",
        "widget": atapi.IntegerWidget(
            label=_("Integer Field"),
            description=_("Enter a number")
        ),
    })

# 自动匹配字段类型的函数
def determine_field_type(field_name):
    """Determine field type based on field name"""
    name = field_name.lower()
    if any(keyword in name for keyword in ["date", "time"]):
        return DateField
    elif any(keyword in name for keyword in ["yes", "no", "true", "false", "comprehensive", "suitable"]):
        return BooleanField
    elif any(keyword in name for keyword in ["number", "count", "score", "quantity", "age", "line"]):
        return IntegerField
    else:
        return LabelField

# 自定义采样字段类
class CustomSamplingField(RecordField):
    security = ClassSecurityInfo()
    _properties = RecordField._properties.copy()

    field_names = [
        "serial_number", "patient_number", "unique_id", "screening_date", "name_abbreviation",
        "gender", "age", "cancer_diagnosis", "lesion_description", "ecog_score",
        "comorbidities", "admission_date", "admission_department", "department_head",
        "disease_screening", "screening_report_date", "blood_collection_date",
        "treatment_lines", "treatment_summary", "sample_type", "sample_quantity",
        "collection_date", "delivery_date", "logistics_number", "transport_conditions",
        "hla_status", "target_expression", "comprehensive_screening", "suitable_for_screening",
        "screening_reason", "enrollment_status", "remarks", "follow_up"
    ]

    _properties.update({
        "type": "customsampling",
        "subfields": tuple(field_names),
        "subfield_labels": {
            field_name: _(field_name.replace("_", " ").title()) for field_name in field_names
        },
        "subfield_types": {
            field_name: determine_field_type(field_name).__name__ for field_name in field_names
        }
    })

registerField(CustomSamplingField,
              title="Custom Sampling Field",
              description="Field for recording custom sample information")

