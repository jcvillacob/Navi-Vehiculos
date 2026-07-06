from app.clients.quickserve_client import (
    _parse_dataplate,
    extract_marketing_model_name,
    extract_service_model_name,
)


def test_parse_dataplate_keeps_marketing_and_service_model_labels():
    html = """
    <table>
      <tr>
        <td>Marketing Model Name</td>
        <td>Service Model Name</td>
        <td>EPA Model Name</td>
      </tr>
      <tr>
        <td>L9 370</td>
        <td>L9 CM2450 L126B</td>
        <td>Not Available</td>
      </tr>
      <tr>
        <td>Shop Order</td>
        <td>Build Plant</td>
        <td>Build Date</td>
      </tr>
      <tr>
        <td>SOC3217</td>
        <td>CNS - ROCKY MOUNT (CDC)</td>
        <td>2024-04-10T00:00:00Z</td>
      </tr>
    </table>
    """

    dataplate = _parse_dataplate(html)

    assert dataplate["Marketing Model Name"] == "L9 370"
    assert dataplate["Service Model Name"] == "L9 CM2450 L126B"
    assert "L9 370" not in dataplate
    assert extract_marketing_model_name(dataplate) == "L9 370"
    assert extract_service_model_name(dataplate) == "L9 CM2450 L126B"


def test_parse_dataplate_recovers_model_headers_when_value_row_was_isolated():
    html = """
    <table>
      <tr>
        <td>ISG13</td>
        <td>X13 CM2670 X122B</td>
        <td>Not Available</td>
      </tr>
      <tr>
        <td>Marketing Engine Configuration #</td>
        <td>Technical Engine Configuration #</td>
      </tr>
      <tr>
        <td>D1K3001BX03</td>
        <td>D1K3001BX03</td>
      </tr>
    </table>
    """

    dataplate = _parse_dataplate(html)

    assert dataplate["Marketing Model Name"] == "ISG13"
    assert dataplate["Service Model Name"] == "X13 CM2670 X122B"
    assert dataplate["EPA Model Name"] == "Not Available"
    assert "ISG13" not in dataplate
    assert extract_marketing_model_name(dataplate) == "ISG13"
    assert extract_service_model_name(dataplate) == "X13 CM2670 X122B"


def test_parse_dataplate_maps_spanish_model_headers_without_extra_pair():
    html = """
    <table>
      <tr>
        <td>Nombre de modelo de marketing</td>
        <td>Nombre del modelo de servicio</td>
        <td>Nombre del modelo EPA</td>
      </tr>
      <tr>
        <td>ISG13</td>
        <td>X13 CM2670 X122B</td>
        <td>Not Available</td>
      </tr>
    </table>
    """

    dataplate = _parse_dataplate(html)

    assert dataplate["Marketing Model Name"] == "ISG13"
    assert dataplate["Service Model Name"] == "X13 CM2670 X122B"
    assert dataplate["EPA Model Name"] == "Not Available"
    assert "Nombre de modelo de marketing" not in dataplate
    assert "ISG13" not in dataplate
