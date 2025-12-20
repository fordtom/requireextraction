from reqif.parser import ReqIFParser

input_file_path = r"C:\Users\Lustre\Downloads\ReqIf_Python\reqif_testfile.reqif"


def print_hierarchy(node, indent=0):
    """Recursively print all hierarchy nodes."""
    prefix = "  " * indent

    print(
        f"{prefix}- Name: {node.long_name} | "
        f"Level: {node.level} | "
        f"SpecObject ID: {node.spec_object}"
    )

    # Recursively print children
    if node.children:
        for child in node.children:
            print_hierarchy(child, indent + 1)


reqif_bundle = ReqIFParser.parse(input_file_path)

for specification in reqif_bundle.core_content.req_if_content.specifications:
    print(f"\nSPECIFICATION: {specification.long_name}")

    for root_node in reqif_bundle.iterate_specification_hierarchy(specification):
        print_hierarchy(root_node)

