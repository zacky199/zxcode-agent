from storage.vector import SimpleVectorStore

class SemanticMemory(SimpleVectorStore):
    """
    Penyimpanan memori semantik jangka panjang. 
    Digunakan agen untuk mengingat struktur kompleks, 
    konvensi kode khusus pengguna, atau perbaikan bug di masa lalu.
    """
    def __init__(self):
        super().__init__()
        # Agen dapat secara eksplisit memanggil self.add_document(id, text)
        # untuk menyimpan potongan logika penting ke dalam memori ini.
